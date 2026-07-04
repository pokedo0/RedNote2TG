from __future__ import annotations

import asyncio
import logging
import re
from html import escape
from pathlib import Path
from typing import Any, ClassVar

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods.base import TelegramMethod
from aiogram.types import Message

from rednote2tg.models import DownloadedMedia, MediaType, Note, PublishResult, PublishStatus


logger = logging.getLogger(__name__)


_DESCRIPTION_MAX_CHARS = 200
_DESCRIPTION_MAX_LINES = 6
_TRUNCATION_MARKER = "...."
_TOPIC_PATTERN = re.compile(r"#([^#\[\]\r\n]+?)\[话题\]#")
_INLINE_SPACE_PATTERN = re.compile(r"[ \t\f\v]+")


class RawSendMediaGroup(TelegramMethod[list[Message]]):
    __returning__: ClassVar[Any] = list[Message]
    __api_method__: ClassVar[str] = "sendMediaGroup"

    chat_id: str
    media: list[dict[str, Any]]
    reply_to_message_id: int | None = None


class TelegramPublisher:
    def __init__(
        self,
        bot: Any,
        channel_id: str,
        parse_mode: str = "HTML",
        retries: int = 2,
        retry_after_padding_seconds: float = 1.0,
    ):
        self.bot = bot
        self.channel_id = channel_id
        self.parse_mode = parse_mode
        self.retries = retries
        self.retry_after_padding_seconds = retry_after_padding_seconds
        self.telegram_retry_after_count = 0

    async def publish_note(self, note: Note, media: list[DownloadedMedia], chat_id: str | int | None = None) -> PublishResult:
        caption = render_caption(note)
        target_chat_id = chat_id if chat_id is not None else self.channel_id
        try:
            if not media:
                msg = await self._send_with_retry(
                    self.bot.send_message,
                    target_chat_id,
                    caption,
                    parse_mode=self.parse_mode,
                )
                return PublishResult(PublishStatus.SENT_DEGRADED, _message_ids([msg]))
            messages = await self._send_media(media, caption, target_chat_id)
            return PublishResult(PublishStatus.SENT, _message_ids(messages))
        except TelegramRetryAfter as retry_exc:
            return PublishResult(PublishStatus.FAILED, error_message=str(retry_exc))
        except Exception as media_exc:
            try:
                msg = await self._send_with_retry(
                    self.bot.send_message,
                    target_chat_id,
                    caption,
                    parse_mode=self.parse_mode,
                )
                return PublishResult(PublishStatus.SENT_DEGRADED, _message_ids([msg]), str(media_exc))
            except TelegramRetryAfter as retry_exc:
                return PublishResult(PublishStatus.FAILED, error_message=str(retry_exc))
            except Exception as text_exc:
                return PublishResult(PublishStatus.FAILED, error_message=str(text_exc))

    async def _send_with_retry(self, send, *args, **kwargs):
        for attempt in range(self.retries + 1):
            try:
                return await send(*args, **kwargs)
            except TelegramRetryAfter as exc:
                self.telegram_retry_after_count += 1
                if attempt >= self.retries:
                    raise
                sleep_seconds = exc.retry_after + self.retry_after_padding_seconds
                logger.warning(
                    "telegram flood limit hit: method=%s attempt=%d/%d retry_after=%s sleep_seconds=%s",
                    getattr(send, "__name__", type(send).__name__),
                    attempt + 1,
                    self.retries + 1,
                    exc.retry_after,
                    sleep_seconds,
                )
                await asyncio.sleep(sleep_seconds)
        raise RuntimeError("telegram retry loop exited unexpectedly")

    async def send_debug_message(self, text: str):
        return await self._send_with_retry(
            self.bot.send_message,
            self.channel_id,
            text,
            parse_mode=None,
        )

    async def _send_media(self, media: list[DownloadedMedia], caption: str, chat_id: str | int):
        if len(media) == 1:
            return [await self._send_single_media(media[0], caption, chat_id)]

        all_messages = []
        reply_to_message_id = None
        for index, chunk in enumerate(chunk_media(media, 10)):
            if index > 0:
                await asyncio.sleep(1)
            result = await self._send_media_chunk(
                chunk,
                caption if index == 0 else None,
                chat_id,
                reply_to_message_id,
            )
            all_messages.extend(result or [])
            if result:
                reply_to_message_id = getattr(result[-1], "message_id", None)
        return all_messages

    async def _send_single_media(self, item: DownloadedMedia, caption: str, chat_id: str | int):
        payload = _file_payload(item.path)
        if _can_send_live_photo(item):
            return await self._send_with_retry(
                self.bot.send_live_photo,
                chat_id,
                _file_payload(item.live_video_path),
                payload,
                caption=caption,
                parse_mode=self.parse_mode,
            )
        send = self.bot.send_video if item.item.media_type is MediaType.VIDEO else self.bot.send_photo
        return await self._send_with_retry(
            send,
            chat_id,
            payload,
            caption=caption,
            parse_mode=self.parse_mode,
        )

    async def _send_media_chunk(
        self,
        chunk: list[DownloadedMedia],
        caption: str | None,
        chat_id: str | int,
        reply_to_message_id: int | None = None,
    ):
        use_raw_group = _needs_raw_media_group(chunk)
        group = [
            _album_input_media(
                item,
                caption if item_index == 0 else None,
                self.parse_mode,
                use_raw_group,
            )
            for item_index, item in enumerate(chunk)
        ]
        if use_raw_group:
            return await self._send_with_retry(
                self.bot.__call__,
                RawSendMediaGroup(chat_id=str(chat_id), media=group, reply_to_message_id=reply_to_message_id),
            )
        return await self._send_with_retry(
            self.bot.send_media_group,
            chat_id,
            group,
            reply_to_message_id=reply_to_message_id,
        )


def render_caption(note: Note) -> str:
    description, topics = _format_description_and_topics(note.description)
    lines = [
        f"<b>{escape(note.display_title)}</b>",
    ]
    if description:
        lines.append(escape(description))
    if topics:
        lines.append(_topics_block(topics))
    counts = _counts_line(note)
    if counts:
        lines.append(counts)
    lines.append(f'<a href="{escape(note.url, quote=True)}">原文</a>')
    return "\n".join(lines)


def chunk_media(media: list[DownloadedMedia], size: int = 10) -> list[list[DownloadedMedia]]:
    return [media[index:index + size] for index in range(0, len(media), size)]


def _format_description_and_topics(description: str) -> tuple[str, list[str]]:
    topics: list[str] = []

    def collect_topic(match: re.Match[str]) -> str:
        topic = match.group(1).strip()
        if topic:
            topics.append(f"#{topic}")
        return " "

    description_without_topics = _TOPIC_PATTERN.sub(collect_topic, description)
    return _truncate_description(_clean_description(description_without_topics)), topics


def _clean_description(description: str) -> str:
    lines = []
    for raw_line in description.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _INLINE_SPACE_PATTERN.sub(" ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _truncate_description(description: str) -> str:
    lines = description.splitlines()
    truncated = len(lines) > _DESCRIPTION_MAX_LINES
    text = "\n".join(lines[:_DESCRIPTION_MAX_LINES])
    if len(text) > _DESCRIPTION_MAX_CHARS:
        text = text[: _DESCRIPTION_MAX_CHARS - len(_TRUNCATION_MARKER)].rstrip()
        truncated = True
    if truncated:
        text = text[: _DESCRIPTION_MAX_CHARS - len(_TRUNCATION_MARKER)].rstrip()
        return f"{text}{_TRUNCATION_MARKER}"
    return text


def _topics_block(topics: list[str]) -> str:
    return f"<blockquote><b>{escape(' '.join(topics))}</b></blockquote>"


def _counts_line(note: Note) -> str:
    parts = []
    if note.liked_count is not None:
        parts.append(f"赞 {escape(str(note.liked_count))}")
    if note.collected_count is not None:
        parts.append(f"藏 {escape(str(note.collected_count))}")
    if note.comment_count is not None:
        parts.append(f"评 {escape(str(note.comment_count))}")
    if note.share_count is not None:
        parts.append(f"转 {escape(str(note.share_count))}")
    return " / ".join(parts)


def _file_payload(path: Path):
    try:
        from aiogram.types import FSInputFile

        return FSInputFile(path)
    except Exception:
        return str(path)


def _input_media(item: DownloadedMedia, caption: str | None, parse_mode: str):
    payload = _file_payload(item.path)
    try:
        from aiogram.types import InputMediaPhoto, InputMediaVideo

        if item.item.media_type is MediaType.VIDEO:
            return InputMediaVideo(media=payload, caption=caption, parse_mode=parse_mode if caption else None)
        return InputMediaPhoto(media=payload, caption=caption, parse_mode=parse_mode if caption else None)
    except Exception:
        return {
            "type": "video" if item.item.media_type is MediaType.VIDEO else "photo",
            "media": payload,
            "caption": caption,
            "parse_mode": parse_mode if caption else None,
        }


def _album_input_media(
    item: DownloadedMedia,
    caption: str | None,
    parse_mode: str,
    use_raw_group: bool,
):
    if use_raw_group:
        return _raw_input_media(item, caption, parse_mode)
    return _input_media(item, caption, parse_mode)


def _raw_input_media(item: DownloadedMedia, caption: str | None, parse_mode: str) -> dict[str, Any]:
    if _can_send_live_photo(item):
        return {
            "type": "live_photo",
            "media": _file_payload(item.live_video_path),
            "photo": _file_payload(item.path),
            "caption": caption,
            "parse_mode": parse_mode if caption else None,
        }
    return {
        "type": "video" if item.item.media_type is MediaType.VIDEO else "photo",
        "media": _file_payload(item.path),
        "caption": caption,
        "parse_mode": parse_mode if caption else None,
    }


def _needs_raw_media_group(items: list[DownloadedMedia]) -> bool:
    return any(_can_send_live_photo(item) for item in items)


def _can_send_live_photo(item: DownloadedMedia) -> bool:
    return item.item.media_type is MediaType.LIVE_PHOTO and item.live_video_path is not None


def _message_ids(messages) -> tuple[int, ...]:
    ids = []
    for msg in messages:
        message_id = getattr(msg, "message_id", None)
        if message_id is not None:
            ids.append(int(message_id))
    return tuple(ids)
