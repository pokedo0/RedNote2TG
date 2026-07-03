from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class PublishStatus(StrEnum):
    SENT = "sent"
    SENT_DEGRADED = "sent_degraded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class SourceRef:
    source_type: str
    source_key: str


@dataclass(frozen=True)
class MediaItem:
    url: str
    media_type: MediaType
    filename_hint: str | None = None


@dataclass(frozen=True)
class Note:
    note_id: str
    url: str
    title: str = ""
    description: str = ""
    author: str = ""
    liked_count: str | int | None = None
    collected_count: str | int | None = None
    comment_count: str | int | None = None
    share_count: str | int | None = None
    upload_time: str | int | None = None
    ip_location: str | None = None
    source: SourceRef = field(default_factory=lambda: SourceRef("", ""))
    media: tuple[MediaItem, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def display_title(self) -> str:
        return self.title.strip() or "无标题"


@dataclass(frozen=True)
class DownloadedMedia:
    item: MediaItem
    path: Path
    size_bytes: int
    content_type: str | None = None


@dataclass(frozen=True)
class PublishResult:
    status: PublishStatus
    telegram_message_ids: tuple[int, ...] = ()
    error_message: str | None = None


@dataclass(frozen=True)
class SourceError:
    source_type: str
    source_key: str
    message: str
