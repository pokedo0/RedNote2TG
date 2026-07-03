from __future__ import annotations

import logging
from time import perf_counter
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rednote2tg.config import AppConfig
from rednote2tg.db import NoteStore
from rednote2tg.keyword_rules import describe_note_time
from rednote2tg.media import MediaDownloader
from rednote2tg.models import PublishResult, PublishStatus
from rednote2tg.telegram_publisher import TelegramPublisher
from rednote2tg.xhs_source import XhsSource

logger = logging.getLogger(__name__)


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
        logger.info("run_once started")
        self.store.cleanup_expired()
        notes, errors = self.source.collect()
        active_ids = self.store.active_note_ids()
        published = 0
        skipped = 0
        failed = 0

        for note in notes:
            if published >= self.config.publishing.notes_per_run:
                break
            if note.note_id in active_ids:
                skipped += 1
                logger.info("note skipped: note_id=%s reason=active_dedup", note.note_id)
                continue

            try:
                logger.info(
                    "note upload started: note_id=%s title=%s media=%d",
                    note.note_id,
                    note.display_title,
                    len(note.media),
                )
                downloads = await self.downloader.download_all(note.note_id, note.media)
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
                logger.info(
                    "note upload finished: note_id=%s status=%s success=true telegram_message_ids=%s",
                    note.note_id,
                    result.status.value,
                    result.telegram_message_ids,
                )
            else:
                failed += 1
                logger.warning(
                    "note upload finished: note_id=%s status=%s success=false reason=%s",
                    note.note_id,
                    result.status.value,
                    result.error_message or "unknown",
                )

        if errors:
            logger.warning("source errors during run: %s", errors)

        elapsed_seconds = perf_counter() - started_at
        keyword_query = getattr(self.source, "last_keyword_query", None)
        summary: dict[str, Any] = {
            "published": published,
            "skipped": skipped,
            "failed": failed,
            "source_errors": len(errors),
            "source_collected_notes": len(notes),
            "source_collected_errors": len(errors),
            "elapsed_seconds": elapsed_seconds,
            "keyword_query": keyword_query.query if keyword_query is not None else "",
            "keyword_note_time": keyword_query.note_time if keyword_query is not None else None,
            "keyword_time_filter": describe_note_time(keyword_query.note_time if keyword_query is not None else None),
        }
        logger.info(format_run_once_summary(summary))
        return summary


def create_scheduler(config: AppConfig, runner: PublishJobRunner):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler(timezone=ZoneInfo(config.schedule.timezone))
    register_schedules(scheduler, config, runner)
    return scheduler


def register_schedules(scheduler, config: AppConfig, runner: PublishJobRunner) -> None:
    timezone = ZoneInfo(config.schedule.timezone)
    for value in config.schedule.times:
        hour, minute = (int(part) for part in value.split(":", 1))
        scheduler.add_job(
            runner.run_once,
            "cron",
            hour=hour,
            minute=minute,
            timezone=timezone,
            id=f"publish-{value}",
            replace_existing=True,
        )


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


def format_run_once_summary(result: dict[str, Any]) -> str:
    return (
        "run_once done:\n"
        f"  source_collected notes={result['source_collected_notes']} errors={result['source_collected_errors']}\n"
        f"  publish published={result['published']} skipped={result['skipped']} failed={result['failed']} "
        f"source_errors={result['source_errors']}\n"
        f"  keyword query={result['keyword_query'] or '-'} time_filter={result['keyword_time_filter']}\n"
        f"  elapsed={result['elapsed_seconds']:.3f}s"
    )


async def handle_status(message, store: NoteStore, scheduler, admin_user_ids: tuple[int, ...]) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not is_authorized(user_id, admin_user_ids):
        await message.answer("unauthorized")
        return
    summary = store.summary(datetime.now(UTC))
    jobs = scheduler.get_jobs() if scheduler is not None else []
    await message.answer(
        "status: "
        f"jobs={len(jobs)} active_dedup={summary.active_dedup_count} "
        f"sent={summary.recent_sent_count} failed={summary.recent_failed_count}"
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


def register_handlers(dispatcher, runner: PublishJobRunner, store: NoteStore, scheduler, admin_user_ids: tuple[int, ...]) -> None:
    try:
        from aiogram.filters import Command
    except Exception as exc:  # pragma: no cover - runtime dependency guard.
        raise RuntimeError("aiogram is required to register bot handlers") from exc

    @dispatcher.message(Command("run_once"))
    async def _run_once(message):
        await handle_run_once(message, runner, admin_user_ids)

    @dispatcher.message(Command("status"))
    async def _status(message):
        await handle_status(message, store, scheduler, admin_user_ids)

    @dispatcher.message(Command("start_tasks"))
    async def _start_tasks(message):
        await handle_start_tasks(message, scheduler, admin_user_ids)

    @dispatcher.message(Command("stop_tasks"))
    async def _stop_tasks(message):
        await handle_stop_tasks(message, scheduler, admin_user_ids)
