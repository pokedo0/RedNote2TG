from __future__ import annotations

import gzip
import logging
import shutil
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rednote2tg.config import LoggingConfig

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(config: LoggingConfig | int | None = None) -> None:
    logging_config = _coerce_config(config)
    level = getattr(logging, logging_config.level)
    formatter = logging.Formatter(LOG_FORMAT)
    handlers: list[logging.Handler] = []

    if logging_config.console_enabled:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    if logging_config.file_enabled:
        file_handler = _create_file_handler(logging_config)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    if not handlers:
        handlers.append(logging.NullHandler())

    root = logging.getLogger()
    for handler in root.handlers:
        handler.close()
    root.handlers = handlers
    root.setLevel(level)
    logging.getLogger("spider_xhs").setLevel(level)


def cleanup_old_logs(config: LoggingConfig) -> None:
    log_path = Path(config.file_path)
    log_dir = log_path.parent
    if not log_dir.exists():
        return

    rotated_logs = _rotated_logs(log_path)
    cutoff = time.time() - config.retention_days * 24 * 60 * 60
    for path in rotated_logs:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            pass

    remaining = [path for path in _rotated_logs(log_path) if path.exists()]
    remaining.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in remaining[config.max_files :]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _create_file_handler(config: LoggingConfig) -> RotatingFileHandler:
    log_path = Path(config.file_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cleanup_old_logs(config)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=config.max_bytes,
        backupCount=config.max_files,
        encoding="utf-8",
    )
    if config.compress_rotated:
        handler.namer = _gzip_name
        handler.rotator = _gzip_rotator
    return handler


def _coerce_config(config: LoggingConfig | int | None) -> LoggingConfig:
    if isinstance(config, LoggingConfig):
        return config
    if isinstance(config, int):
        return LoggingConfig(level=logging.getLevelName(config))
    return LoggingConfig()


def _gzip_name(default_name: str) -> str:
    return f"{default_name}.gz"


def _gzip_rotator(source: str, dest: str) -> None:
    with open(source, "rb") as src, gzip.open(dest, "wb") as dst:
        shutil.copyfileobj(src, dst)
    Path(source).unlink()


def _rotated_logs(log_path: Path) -> list[Path]:
    return [
        path
        for path in log_path.parent.glob(f"{log_path.name}.*")
        if path.name != log_path.name
    ]
