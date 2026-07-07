from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    channel_id: str
    admin_user_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class XhsConfig:
    cookies: str
    proxies: dict[str, str] | None = None


@dataclass(frozen=True)
class KeywordSourceConfig:
    enabled: bool = True
    rules_path: str = ""
    search_limit_per_query: int = 20
    sort_type: int = 0
    note_type: int = 0


@dataclass(frozen=True)
class HomefeedSourceConfig:
    enabled: bool = False
    categories: tuple[str, ...] = ()
    limit_per_category: int = 20


@dataclass(frozen=True)
class SourcesConfig:
    keywords: KeywordSourceConfig
    homefeed: HomefeedSourceConfig


@dataclass(frozen=True)
class PublishingConfig:
    notes_per_run: int = 3
    media_strategy: str = "all"
    caption_parse_mode: str = "HTML"
    telegram_retry_after_padding_seconds: float = 1.0
    upload_live_photo: bool = True


@dataclass(frozen=True)
class DedupConfig:
    ttl_days: int = 14


@dataclass(frozen=True)
class QuietWindowConfig:
    start: str
    end: str


@dataclass(frozen=True)
class ScheduleConfig:
    timezone: str
    interval_minutes: int
    jitter_minutes: int
    quiet_window: QuietWindowConfig


@dataclass(frozen=True)
class StorageConfig:
    sqlite_path: str = "data/rednote2tg.db"
    media_temp_dir: str = "data/tmp_media"


@dataclass(frozen=True)
class DebugConfig:
    enabled: bool = False


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    console_enabled: bool = True
    file_enabled: bool = True
    file_path: str = "logs/rednote2tg.log"
    max_bytes: int = 5 * 1024 * 1024
    retention_days: int = 14
    max_files: int = 20
    compress_rotated: bool = True


@dataclass(frozen=True)
class AppConfig:
    telegram: TelegramConfig
    xhs: XhsConfig
    sources: SourcesConfig
    publishing: PublishingConfig
    dedup: DedupConfig
    schedule: ScheduleConfig
    storage: StorageConfig
    debug: DebugConfig
    logging: LoggingConfig


_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def load_config(path: str | Path = "config/config.yaml") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")
    return parse_config(data, base_path=config_path.parent)


def parse_config(data: dict, base_path: str | Path | None = None) -> AppConfig:
    telegram = _parse_telegram(data.get("telegram") or {})
    xhs = _parse_xhs(data.get("xhs") or {})
    sources = _parse_sources(data.get("sources") or {}, base_path=base_path)
    publishing = _parse_publishing(data.get("publishing") or {})
    dedup = _parse_dedup(data.get("dedup") or {})
    schedule = _parse_schedule(data.get("schedule") or {})
    storage = _parse_storage(data.get("storage") or {})
    debug = _parse_debug(data.get("debug") or {})
    logging_config = _parse_logging(data.get("logging") or {})
    return AppConfig(
        telegram=telegram,
        xhs=xhs,
        sources=sources,
        publishing=publishing,
        dedup=dedup,
        schedule=schedule,
        storage=storage,
        debug=debug,
        logging=logging_config,
    )


def _parse_telegram(data: dict) -> TelegramConfig:
    token = str(data.get("bot_token") or "").strip()
    channel = str(data.get("channel_id") or "").strip()
    if not token:
        raise ConfigError("telegram.bot_token is required")
    if not channel:
        raise ConfigError("telegram.channel_id is required")
    admins = tuple(int(value) for value in data.get("admin_user_ids") or ())
    return TelegramConfig(token, channel, admins)


def _parse_xhs(data: dict) -> XhsConfig:
    cookies = str(data.get("cookies") or "").strip()
    if not cookies:
        raise ConfigError("xhs.cookies is required")
    proxies = data.get("proxies")
    if proxies is not None and not isinstance(proxies, dict):
        raise ConfigError("xhs.proxies must be null or a mapping")
    return XhsConfig(cookies, proxies)


def _parse_sources(data: dict, base_path: str | Path | None = None) -> SourcesConfig:
    keywords_data = data.get("keywords") or {}
    homefeed_data = data.get("homefeed") or {}
    keywords_enabled = bool(keywords_data.get("enabled", True))
    rules_path = str(keywords_data.get("rules_path") or "").strip()
    if keywords_enabled and not rules_path:
        raise ConfigError("sources.keywords.rules_path is required when keyword source is enabled")
    if rules_path and base_path is not None and not _is_url(rules_path):
        path = Path(rules_path)
        if not path.is_absolute():
            rules_path = str(Path(base_path) / path)
    keywords = KeywordSourceConfig(
        enabled=keywords_enabled,
        rules_path=rules_path,
        search_limit_per_query=_positive_int(keywords_data.get("search_limit_per_query", 20), "sources.keywords.search_limit_per_query"),
        sort_type=int(keywords_data.get("sort_type", 0)),
        note_type=int(keywords_data.get("note_type", 0)),
    )
    homefeed = HomefeedSourceConfig(
        enabled=bool(homefeed_data.get("enabled", False)),
        categories=tuple(str(c).strip() for c in homefeed_data.get("categories") or () if str(c).strip()),
        limit_per_category=_positive_int(homefeed_data.get("limit_per_category", 20), "sources.homefeed.limit_per_category"),
    )
    return SourcesConfig(keywords=keywords, homefeed=homefeed)


def _parse_publishing(data: dict) -> PublishingConfig:
    notes_per_run = _positive_int(data.get("notes_per_run", 3), "publishing.notes_per_run")
    if not 1 <= notes_per_run <= 10:
        raise ConfigError("publishing.notes_per_run must be between 1 and 10")
    return PublishingConfig(
        notes_per_run=notes_per_run,
        media_strategy=str(data.get("media_strategy", "all")),
        caption_parse_mode=str(data.get("caption_parse_mode", "HTML")),
        telegram_retry_after_padding_seconds=_nonnegative_float(
            data.get("telegram_retry_after_padding_seconds", 1.0),
            "publishing.telegram_retry_after_padding_seconds",
        ),
        upload_live_photo=_bool(data.get("upload_live_photo", True), "publishing.upload_live_photo"),
    )


def _parse_dedup(data: dict) -> DedupConfig:
    ttl_days = _positive_int(data.get("ttl_days", 14), "dedup.ttl_days")
    if not 7 <= ttl_days <= 14:
        raise ConfigError("dedup.ttl_days must be between 7 and 14")
    return DedupConfig(ttl_days=ttl_days)


def _parse_schedule(data: dict) -> ScheduleConfig:
    timezone = str(data.get("timezone", "Asia/Shanghai"))
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"unknown schedule.timezone: {timezone}") from exc
    if "times" in data:
        raise ConfigError("schedule.times is no longer supported; use schedule.interval_minutes")
    if "interval_minutes" not in data:
        raise ConfigError("schedule.interval_minutes is required")

    quiet_data = data.get("quiet_window")
    if not isinstance(quiet_data, dict):
        raise ConfigError("schedule.quiet_window is required")

    quiet_start = str(quiet_data.get("start") or "").strip()
    quiet_end = str(quiet_data.get("end") or "").strip()
    quiet_start_minutes = _time_to_minutes(quiet_start)
    quiet_end_minutes = _time_to_minutes(quiet_end)
    if quiet_start_minutes == quiet_end_minutes:
        raise ConfigError("schedule.quiet_window.start must not equal schedule.quiet_window.end")

    return ScheduleConfig(
        timezone=timezone,
        interval_minutes=_positive_int(data.get("interval_minutes"), "schedule.interval_minutes"),
        jitter_minutes=_nonnegative_int(data.get("jitter_minutes", 0), "schedule.jitter_minutes"),
        quiet_window=QuietWindowConfig(start=quiet_start, end=quiet_end),
    )


def _parse_storage(data: dict) -> StorageConfig:
    return StorageConfig(
        sqlite_path=str(data.get("sqlite_path", "data/rednote2tg.db")),
        media_temp_dir=str(data.get("media_temp_dir", "data/tmp_media")),
    )


def _parse_debug(data: dict) -> DebugConfig:
    return DebugConfig(enabled=_bool(data.get("enabled", False), "debug.enabled"))


def _parse_logging(data: dict) -> LoggingConfig:
    level = str(data.get("level", "INFO")).strip().upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("logging.level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
    file_path = str(data.get("file_path", "logs/rednote2tg.log")).strip()
    if not file_path:
        raise ConfigError("logging.file_path is required")
    return LoggingConfig(
        level=level,
        console_enabled=_bool(data.get("console_enabled", True), "logging.console_enabled"),
        file_enabled=_bool(data.get("file_enabled", True), "logging.file_enabled"),
        file_path=file_path,
        max_bytes=_positive_int(data.get("max_bytes", 5 * 1024 * 1024), "logging.max_bytes"),
        retention_days=_positive_int(data.get("retention_days", 14), "logging.retention_days"),
        max_files=_positive_int(data.get("max_files", 20), "logging.max_files"),
        compress_rotated=_bool(data.get("compress_rotated", True), "logging.compress_rotated"),
    )


def _positive_int(value: object, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ConfigError(f"{name} must be greater than zero")
    return parsed


def _nonnegative_float(value: object, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if parsed < 0:
        raise ConfigError(f"{name} must be greater than or equal to zero")
    return parsed


def _nonnegative_int(value: object, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise ConfigError(f"{name} must be greater than or equal to zero")
    return parsed


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean")
    return value


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _validate_time(value: str) -> None:
    if not _TIME_RE.match(value):
        raise ConfigError(f"schedule time must use HH:MM format: {value}")
    hour, minute = (int(part) for part in value.split(":", 1))
    if hour > 23 or minute > 59:
        raise ConfigError(f"schedule time is out of range: {value}")


def _time_to_minutes(value: str) -> int:
    _validate_time(value)
    hour, minute = (int(part) for part in value.split(":", 1))
    return hour * 60 + minute
