from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from rednote2tg.config import AppConfig, XhsConfig, load_config
from rednote2tg.db import NoteStore
from rednote2tg.keyword_rules import describe_note_time, load_keyword_rules
from rednote2tg.media import MediaDownloader
from rednote2tg.models import PublishResult, PublishStatus
from rednote2tg.telegram_publisher import TelegramPublisher
from rednote2tg.xhs_source import XhsSource

logger = logging.getLogger(__name__)
XHS_URL_PATTERN = re.compile(r"https?://[^\s<>]*xiaohongshu\.com/[^\s<>]+")
SOURCE_ERROR_AUTO_PAUSE_THRESHOLD = 3


class PublishJobRunner:
    def __init__(
        self,
        config: AppConfig,
        source: XhsSource,
        store: NoteStore,
        downloader: MediaDownloader,
        publisher: TelegramPublisher,
    ):
        self.config = config
        self.source = source
        self.store = store
        self.downloader = downloader
        self.publisher = publisher

    async def run_once(self) -> dict[str, Any]:
        started_at = perf_counter()
        retry_after_start = getattr(self.publisher, "telegram_retry_after_count", 0)
        logger.info("run_once started")
        self.store.cleanup_expired()
        active_ids = self.store.active_note_ids()
        notes, errors = self.source.collect(active_note_ids=set(active_ids))
        published = 0
        published_media = 0
        skipped = getattr(self.source, "last_pre_detail_dedup_skipped", 0)
        failed = 0
        failed_media = 0
        pending_retry_after_seconds: float | None = None
        wait_before_next_note = False

        for note in notes:
            if published >= self.config.publishing.notes_per_run:
                break
            if note.note_id in active_ids:
                skipped += 1
                logger.info("note skipped: note_id=%s reason=active_dedup", note.note_id)
                continue

            if wait_before_next_note:
                note_interval_seconds = self.config.publishing.note_interval_seconds
                if note_interval_seconds > 0:
                    logger.info(
                        "note interval before next upload: sleep_seconds=%s",
                        note_interval_seconds,
                    )
                    await asyncio.sleep(note_interval_seconds)
                wait_before_next_note = False

            if pending_retry_after_seconds is not None:
                padding_seconds = getattr(self.publisher, "retry_after_padding_seconds", 0.0)
                sleep_seconds = pending_retry_after_seconds + padding_seconds
                logger.warning(
                    "telegram retry-after cooldown before next note: retry_after=%s sleep_seconds=%s",
                    pending_retry_after_seconds,
                    sleep_seconds,
                )
                await asyncio.sleep(sleep_seconds)
                pending_retry_after_seconds = None

            try:
                logger.info(
                    "note upload started: note_id=%s title=%s media=%d",
                    note.note_id,
                    note.display_title,
                    len(note.media),
                )
                downloads = await self.downloader.download_all(
                    note.note_id,
                    note.media,
                    upload_live_photo=self.config.publishing.upload_live_photo,
                )
                result = await self.publisher.publish_note(note, downloads)
            except Exception as exc:
                logger.exception("note publish failed: %s", note.note_id)
                result = PublishResult(PublishStatus.FAILED, error_message=str(exc))
            finally:
                self.downloader.cleanup()

            if result.status in {PublishStatus.SENT, PublishStatus.SENT_DEGRADED}:
                self.store.record_publish(note, result, self.config.dedup.ttl_days)
                active_ids.add(note.note_id)
                published += 1
                published_media += len(note.media)
                if result.error_message:
                    logger.info(
                        "note upload finished: note_id=%s status=%s success=true telegram_message_ids=%s error_message=%s",
                        note.note_id,
                        result.status.value,
                        result.telegram_message_ids,
                        result.error_message,
                    )
                else:
                    logger.info(
                        "note upload finished: note_id=%s status=%s success=true telegram_message_ids=%s",
                        note.note_id,
                        result.status.value,
                        result.telegram_message_ids,
                    )
            else:
                failed += 1
                failed_media += len(note.media)
                pending_retry_after_seconds = result.retry_after_seconds
                logger.warning(
                    "note upload finished: note_id=%s status=%s success=false reason=%s",
                    note.note_id,
                    result.status.value,
                    result.error_message or "unknown",
                )
            wait_before_next_note = True

        if errors:
            logger.warning("source errors during run: %s", errors)

        elapsed_seconds = perf_counter() - started_at
        keyword_query = getattr(self.source, "last_keyword_query", None)
        keyword_rule = getattr(self.source, "last_keyword_rule_name", "")
        summary: dict[str, Any] = {
            "published": published,
            "published_media": published_media,
            "skipped": skipped,
            "failed": failed,
            "failed_media": failed_media,
            "source_errors": len(errors),
            "source_collected_notes": len(notes),
            "source_collected_errors": len(errors),
            "elapsed_seconds": elapsed_seconds,
            "keyword_query": keyword_query.query if keyword_query is not None else "",
            "keyword_rule": keyword_rule,
            "keyword_note_time": keyword_query.note_time if keyword_query is not None else None,
            "keyword_time_filter": describe_note_time(keyword_query.note_time if keyword_query is not None else None),
            "telegram_retry_after_count": max(
                0,
                getattr(self.publisher, "telegram_retry_after_count", retry_after_start)
                - retry_after_start,
            ),
        }
        logger.info(format_run_once_summary(summary))
        if self.config.debug.enabled and published > 0:
            try:
                await self.publisher.send_debug_message(format_run_once_summary(summary))
            except Exception:
                logger.exception("debug summary send failed")
        return summary


@dataclass
class RuntimeState:
    config: AppConfig
    runner: PublishJobRunner
    store: NoteStore
    scheduler: Any | None
    config_path: str = "config/config.yaml"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def publisher(self):
        return self.runner.publisher

    async def run_once(self) -> dict[str, Any]:
        async with self.lock:
            return await self.runner.run_once()


def create_scheduler(config: AppConfig, runner):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler(timezone=ZoneInfo(config.schedule.timezone))
    register_schedules(scheduler, config, runner)
    return scheduler


class SourceErrorAutoPause:
    def __init__(self, runner: PublishJobRunner, scheduler, threshold: int = SOURCE_ERROR_AUTO_PAUSE_THRESHOLD):
        self.runner = runner
        self.scheduler = scheduler
        self.threshold = threshold
        self.consecutive_source_error_runs = 0

    async def run_once(self) -> dict[str, Any]:
        summary = await self.runner.run_once()
        if summary.get("source_errors", 0) > 0:
            self.consecutive_source_error_runs += 1
            logger.warning(
                "source error streak: count=%d threshold=%d",
                self.consecutive_source_error_runs,
                self.threshold,
            )
        else:
            self.consecutive_source_error_runs = 0

        if self.consecutive_source_error_runs >= self.threshold:
            self.scheduler.pause()
            logger.error(
                "scheduled crawl tasks paused after consecutive source errors: threshold=%d",
                self.threshold,
            )
            await self._send_debug_pause_message()
            self.consecutive_source_error_runs = 0
        return summary

    async def _send_debug_pause_message(self) -> None:
        config = getattr(self.runner, "config", None)
        if not getattr(getattr(config, "debug", None), "enabled", False):
            return
        publisher = getattr(self.runner, "publisher", None)
        if publisher is None:
            return
        try:
            await publisher.send_debug_message(
                f"定时爬取任务已自动暂停：连续 {self.threshold} 次爬取异常，请检查小红书登录状态后使用 /start_tasks 恢复。"
            )
        except Exception:
            logger.exception("debug auto-pause notification send failed")


def register_schedules(scheduler, config: AppConfig, runner: PublishJobRunner) -> None:
    timezone = ZoneInfo(config.schedule.timezone)
    quiet_start = _time_to_minutes(config.schedule.quiet_window.start)
    quiet_end = _time_to_minutes(config.schedule.quiet_window.end)
    jitter_seconds = config.schedule.jitter_minutes * 60
    guarded_runner = SourceErrorAutoPause(runner, scheduler)
    for minute_of_day in _scheduled_minutes(
        config.schedule.interval_minutes,
        config.schedule.jitter_minutes,
        quiet_start,
        quiet_end,
    ):
        hour, minute = divmod(minute_of_day, 60)
        time_value = f"{hour:02d}:{minute:02d}"
        scheduler.add_job(
            guarded_runner.run_once,
            "cron",
            hour=hour,
            minute=minute,
            timezone=timezone,
            jitter=jitter_seconds,
            id=f"publish-{time_value}",
            replace_existing=True,
        )


def _scheduled_minutes(
    interval_minutes: int,
    jitter_minutes: int,
    quiet_start: int,
    quiet_end: int,
) -> tuple[int, ...]:
    return tuple(
        minute
        for minute in range(0, 24 * 60, interval_minutes)
        if not _minute_in_quiet_window(minute, quiet_start, quiet_end)
        and not _jitter_can_enter_quiet_window(minute, jitter_minutes, quiet_start, quiet_end)
    )


def _jitter_can_enter_quiet_window(base_minute: int, jitter_minutes: int, quiet_start: int, quiet_end: int) -> bool:
    if jitter_minutes >= 24 * 60:
        return True
    return any(
        _minute_in_quiet_window(base_minute + offset, quiet_start, quiet_end)
        for offset in range(jitter_minutes + 1)
    )


def _minute_in_quiet_window(minute: int, quiet_start: int, quiet_end: int) -> bool:
    minute %= 24 * 60
    if quiet_start < quiet_end:
        return quiet_start <= minute < quiet_end
    return minute >= quiet_start or minute < quiet_end


def _time_to_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":", 1))
    return hour * 60 + minute


def is_authorized(user_id: int | None, admin_user_ids: tuple[int, ...]) -> bool:
    if not admin_user_ids:
        return True
    return user_id in admin_user_ids


async def handle_run_once(message, runner: PublishJobRunner, admin_user_ids: tuple[int, ...]) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_authorized(user_id, admin_user_ids):
        await message.answer("unauthorized")
        return
    result = await runner.run_once()
    await message.answer(format_run_once_summary(result))


async def handle_runtime_run_once(message, state: RuntimeState) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_authorized(user_id, state.config.telegram.admin_user_ids):
        await message.answer("unauthorized")
        return
    result = await state.run_once()
    await message.answer(format_run_once_summary(result))


def format_run_once_summary(result: dict[str, Any]) -> str:
    return (
        "run_once done:\n"
        f"  source_collected notes={result['source_collected_notes']} errors={result['source_collected_errors']}\n"
        f"  publish published={result['published']}(media={result.get('published_media', 0)}) "
        f"skipped={result['skipped']} failed={result['failed']}(media={result.get('failed_media', 0)}) "
        f"source_errors={result['source_errors']}\n"
        f"  keyword rule={result.get('keyword_rule') or '-'} query={result['keyword_query'] or '-'} time_filter={result['keyword_time_filter']}\n"
        f"  TelegramRetryAfter count={result.get('telegram_retry_after_count', 0)}\n"
        f"  elapsed={result['elapsed_seconds']:.3f}s"
    )


async def handle_status(
    message,
    store: NoteStore,
    scheduler,
    admin_user_ids: tuple[int, ...],
    config: AppConfig | None = None,
) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_authorized(user_id, admin_user_ids):
        await message.answer("unauthorized")
        return
    summary = store.summary(datetime.now(UTC))
    jobs = scheduler.get_jobs() if scheduler is not None else []
    timezone = ZoneInfo(config.schedule.timezone) if config is not None else UTC
    await message.answer(
        "status: "
        f"crawl={_format_crawl_status(scheduler)} jobs={len(jobs)} next_run={_format_next_run(jobs, timezone)} "
        f"{_format_schedule_status(config)} active_dedup={summary.active_dedup_count} "
        f"sent={summary.recent_sent_count} failed={summary.recent_failed_count}"
    )


def _format_next_run(jobs, timezone) -> str:
    next_runs = [getattr(job, "next_run_time", None) for job in jobs]
    next_runs = [value for value in next_runs if value is not None]
    if not next_runs:
        return "-"
    return min(next_runs).astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_crawl_status(scheduler) -> str:
    if scheduler is None:
        return "unconfigured"
    if getattr(scheduler, "paused", False):
        return "paused"
    state = getattr(scheduler, "state", None)
    if state == 2:
        return "paused"
    if state == 1:
        return "running"
    if state == 0:
        return "stopped"
    if getattr(scheduler, "running", False):
        return "running"
    return "unknown"


def _format_schedule_status(config: AppConfig | None) -> str:
    if config is None:
        return "schedule=-"
    schedule = config.schedule
    return (
        "schedule=interval "
        f"interval={schedule.interval_minutes}m "
        f"jitter=0-{schedule.jitter_minutes}m "
        f"quiet={schedule.quiet_window.start}-{schedule.quiet_window.end}"
    )


async def handle_start_tasks(message, scheduler, admin_user_ids: tuple[int, ...]) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_authorized(user_id, admin_user_ids):
        await message.answer("unauthorized")
        return
    if scheduler:
        scheduler.resume()
        await message.answer("定时爬取任务已启动 (resumed)")
    else:
        await message.answer("未配置调度器")


async def handle_stop_tasks(message, scheduler, admin_user_ids: tuple[int, ...]) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_authorized(user_id, admin_user_ids):
        await message.answer("unauthorized")
        return
    if scheduler:
        scheduler.pause()
        await message.answer("定时爬取任务已暂停 (paused)")
    else:
        await message.answer("未配置调度器")


async def handle_fetch_note(message, runner: PublishJobRunner, admin_user_ids: tuple[int, ...]) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_authorized(user_id, admin_user_ids):
        await message.answer("unauthorized")
        return
    chat_type = getattr(getattr(message, "chat", None), "type", None)
    if chat_type is not None and chat_type != "private":
        await message.answer("请私聊发送 /note <小红书笔记链接>")
        return

    url = extract_xhs_url(getattr(message, "text", "") or "")
    if not url:
        await message.answer("用法：/note <小红书笔记链接>")
        return

    try:
        note = runner.source.fetch_note_url(url)
    except Exception as exc:  # pragma: no cover - exact XHS exceptions vary.
        logger.exception("manual note fetch failed: %s", url)
        await message.answer(f"抓取失败：{exc}")
        return
    if note is None:
        await message.answer("未解析到笔记内容")
        return

    chat_id = getattr(getattr(message, "chat", None), "id", None) or user_id
    try:
        downloads = await runner.downloader.download_all(
            note.note_id,
            note.media,
            upload_live_photo=runner.config.publishing.upload_live_photo,
        )
        result = await runner.publisher.publish_note(note, downloads, chat_id=chat_id)
    except Exception as exc:
        logger.exception("manual note publish failed: %s", url)
        await message.answer(f"发送失败：{exc}")
        return
    finally:
        runner.downloader.cleanup()

    if result.status is PublishStatus.FAILED:
        await message.answer(f"发送失败：{result.error_message or 'unknown'}")


def extract_xhs_url(text: str) -> str | None:
    match = XHS_URL_PATTERN.search(text)
    if match is None:
        return None
    return match.group(0).rstrip(".,;，。；")


async def handle_ping(message, runner: PublishJobRunner, admin_user_ids: tuple[int, ...]) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_authorized(user_id, admin_user_ids):
        await message.answer("unauthorized")
        return
    try:
        data = runner.source.client.unread_message()
        # data is the full JSON response dict from XHS API
        unread_counts = []
        if isinstance(data, dict):
            for key, value in data.items():
                if key not in ("success", "msg", "code") and isinstance(value, (int, float)):
                    unread_counts.append(f"  {key}={value}")
        counts_text = "\n".join(unread_counts) if unread_counts else "  (无未读)"
        await message.answer(f"✅ Cookie 有效\n{counts_text}")
    except Exception as exc:
        logger.warning("ping cookie check failed: %s", exc)
        await message.answer(f"❌ Cookie 已失效或请求异常\n{exc}")


async def handle_update_cookie(
    message,
    runner: PublishJobRunner,
    admin_user_ids: tuple[int, ...],
    config_path: str,
) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_authorized(user_id, admin_user_ids):
        await message.answer("unauthorized")
        return

    text = (getattr(message, "text", "") or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "用法：/update_cookie <cookie字符串>\n"
            "示例：/update_cookie loadts=xxx;xsecappid=xhs-pc-web;acw_tc=xxx"
        )
        return

    new_cookie = parts[1].strip()

    # Update config file — preserve comments and formatting
    try:
        config_file = Path(config_path)
        content = config_file.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
        old_cookie = (data.get("xhs") or {}).get("cookies", "")

        if old_cookie and old_cookie in content:
            new_content = content.replace(old_cookie, new_cookie, 1)
        else:
            new_content = re.sub(
                r'(cookies:\s*)(".*?"|\S[^\n]*)',
                lambda m: m.group(1) + '"' + new_cookie + '"',
                content,
                count=1,
            )
        config_file.write_text(new_content, encoding="utf-8")
    except Exception as exc:
        logger.exception("failed to update config file with new cookie")
        await message.answer(f"❌ 配置文件更新失败：{exc}")
        return

    # Rebuild XHS client with new cookie
    try:
        new_xhs_config = XhsConfig(cookies=new_cookie, proxies=runner.config.xhs.proxies)
        runner.source.client = XhsSource._create_client(new_xhs_config)
    except Exception as exc:
        logger.exception("failed to rebuild XHS client with new cookie")
        await message.answer(f"⚠️ Cookie 已写入配置文件，但客户端重建失败：{exc}")
        return

    logger.info("cookie updated successfully via bot command")
    await message.answer("✅ Cookie 已更新并生效")


async def handle_reload(message, state: RuntimeState) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_authorized(user_id, state.config.telegram.admin_user_ids):
        await message.answer("unauthorized")
        return

    async with state.lock:
        try:
            new_config = load_config(state.config_path)
            _validate_reload_config(new_config)
            blocked_changes = _unsupported_reload_changes(state.config, new_config)
        except Exception as exc:
            logger.warning("config reload validation failed: %s", exc)
            await message.answer(f"❌ 热加载失败：{exc}")
            return

        if blocked_changes:
            await message.answer(
                "❌ 热加载失败：以下配置变化需要重启："
                + ", ".join(blocked_changes)
            )
            return

        try:
            _apply_reload_config(state, new_config)
        except Exception as exc:
            logger.exception("config reload apply failed")
            await message.answer(f"❌ 热加载失败：{exc}")
            return

    await message.answer(
        "✅ 配置已热加载\n"
        f"{_format_schedule_status(state.config)}\n"
        f"keyword_rules={_format_keyword_rules_status(state.config)}"
    )


def _validate_reload_config(config: AppConfig) -> None:
    if config.sources.keywords.enabled:
        keyword_config = config.sources.keywords
        if keyword_config.rules:
            for rule in keyword_config.rules:
                load_keyword_rules(rule.rules_path, allow_local_override=False)
            return
        load_keyword_rules(keyword_config.rules_path)


def _format_keyword_rules_status(config: AppConfig) -> str:
    keyword_config = config.sources.keywords
    if keyword_config.rules:
        return ",".join(f"{rule.name}:{rule.rules_path}" for rule in keyword_config.rules)
    return keyword_config.rules_path or "-"


def _unsupported_reload_changes(old: AppConfig, new: AppConfig) -> list[str]:
    changes: list[str] = []
    if old.telegram.bot_token != new.telegram.bot_token:
        changes.append("telegram.bot_token")
    if old.telegram.channel_id != new.telegram.channel_id:
        changes.append("telegram.channel_id")
    if old.telegram.admin_user_ids != new.telegram.admin_user_ids:
        changes.append("telegram.admin_user_ids")
    if old.storage != new.storage:
        changes.append("storage")
    if old.debug != new.debug:
        changes.append("debug")
    if old.logging != new.logging:
        changes.append("logging")
    if old.publishing.media_strategy != new.publishing.media_strategy:
        changes.append("publishing.media_strategy")
    if old.publishing.caption_parse_mode != new.publishing.caption_parse_mode:
        changes.append("publishing.caption_parse_mode")
    return changes


def _apply_reload_config(state: RuntimeState, new_config: AppConfig) -> None:
    old_config = state.config
    new_client = None
    if old_config.xhs != new_config.xhs:
        new_client = XhsSource._create_client(new_config.xhs)

    if old_config.schedule != new_config.schedule and state.scheduler is not None:
        _replace_publish_schedules(state.scheduler, new_config, state)

    state.config = new_config
    state.runner.config = new_config
    state.runner.source.sources_config = new_config.sources
    if new_client is not None:
        state.runner.source.client = new_client
    state.runner.publisher.retry_after_padding_seconds = (
        new_config.publishing.telegram_retry_after_padding_seconds
    )


def _replace_publish_schedules(scheduler, config: AppConfig, runner) -> None:
    was_paused = _is_scheduler_paused(scheduler)
    _clear_publish_jobs(scheduler)
    register_schedules(scheduler, config, runner)
    if was_paused:
        scheduler.pause()


def _clear_publish_jobs(scheduler) -> None:
    jobs = list(scheduler.get_jobs()) if hasattr(scheduler, "get_jobs") else []
    if hasattr(scheduler, "remove_job"):
        for job in jobs:
            job_id = _job_id(job)
            if job_id and job_id.startswith("publish-"):
                scheduler.remove_job(job_id)
        return

    raw_jobs = getattr(scheduler, "jobs", None)
    if isinstance(raw_jobs, list):
        scheduler.jobs = [
            job
            for job in raw_jobs
            if not ((_job_id(job) or "").startswith("publish-"))
        ]


def _job_id(job) -> str | None:
    job_id = getattr(job, "id", None)
    if job_id is not None:
        return str(job_id)
    if isinstance(job, tuple) and len(job) >= 3 and isinstance(job[2], dict):
        value = job[2].get("id")
        return str(value) if value is not None else None
    return None


def _is_scheduler_paused(scheduler) -> bool:
    return getattr(scheduler, "paused", False) or getattr(scheduler, "state", None) == 2


def register_handlers(dispatcher, state: RuntimeState) -> None:
    try:
        from aiogram.filters import Command
    except Exception as exc:  # pragma: no cover - runtime dependency guard.
        raise RuntimeError("aiogram is required to register bot handlers") from exc

    @dispatcher.message(Command("run_once"))
    async def _run_once(message):
        await handle_runtime_run_once(message, state)

    @dispatcher.message(Command("status"))
    async def _status(message):
        await handle_status(message, state.store, state.scheduler, state.config.telegram.admin_user_ids, state.config)

    @dispatcher.message(Command("start_tasks"))
    async def _start_tasks(message):
        await handle_start_tasks(message, state.scheduler, state.config.telegram.admin_user_ids)

    @dispatcher.message(Command("stop_tasks"))
    async def _stop_tasks(message):
        await handle_stop_tasks(message, state.scheduler, state.config.telegram.admin_user_ids)

    @dispatcher.message(Command("note"))
    async def _note(message):
        await handle_fetch_note(message, state.runner, state.config.telegram.admin_user_ids)

    @dispatcher.message(Command("ping"))
    async def _ping(message):
        await handle_ping(message, state.runner, state.config.telegram.admin_user_ids)

    @dispatcher.message(Command("update_cookie"))
    async def _update_cookie(message):
        await handle_update_cookie(message, state.runner, state.config.telegram.admin_user_ids, state.config_path)

    @dispatcher.message(Command("reload"))
    async def _reload(message):
        await handle_reload(message, state)
