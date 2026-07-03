import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramRetryAfter

from rednote2tg.models import DownloadedMedia, MediaItem, MediaType, Note, PublishStatus, SourceRef
from rednote2tg.telegram_publisher import TelegramPublisher, chunk_media, render_caption


class FakeBot:
    def __init__(self):
        self.calls = []
        self.fail_media = False
        self.group_attempts = 0
        self.retry_after_group_failures = 0
        self.next_id = 1

    async def send_message(self, chat_id, text, parse_mode=None):
        self.calls.append(("message", chat_id, text, parse_mode))
        return self._msg()

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None):
        if self.fail_media:
            raise RuntimeError("photo failed")
        self.calls.append(("photo", chat_id, photo, caption, parse_mode))
        return self._msg()

    async def send_video(self, chat_id, video, caption=None, parse_mode=None):
        if self.fail_media:
            raise RuntimeError("video failed")
        self.calls.append(("video", chat_id, video, caption, parse_mode))
        return self._msg()

    async def send_media_group(self, chat_id, media):
        self.group_attempts += 1
        if self.group_attempts <= self.retry_after_group_failures:
            raise retry_after_error(chat_id, 30)
        if self.fail_media:
            raise RuntimeError("group failed")
        self.calls.append(("group", chat_id, media))
        return [self._msg() for _ in media]

    def _msg(self):
        msg = SimpleNamespace(message_id=self.next_id)
        self.next_id += 1
        return msg


def retry_after_error(chat_id="@c", retry_after=30):
    return TelegramRetryAfter(
        method=SimpleNamespace(chat_id=chat_id),
        message=f"Too Many Requests: retry after {retry_after}",
        retry_after=retry_after,
    )


def sample_note(media=()):
    return Note(
        note_id="n1",
        url="https://xhs/n1?x=<bad>",
        title="T <1>",
        description="D & more",
        author="A",
        liked_count=1,
        collected_count=2,
        comment_count=3,
        share_count=4,
        upload_time="today",
        ip_location="Shanghai",
        source=SourceRef("keyword", "k"),
        media=media,
    )


class TelegramPublisherTest(unittest.IsolatedAsyncioTestCase):
    def test_caption_escapes_html_and_links_source(self):
        caption = render_caption(sample_note())

        self.assertIn("T &lt;1&gt;", caption)
        self.assertIn("D &amp; more", caption)
        self.assertIn('<a href="https://xhs/n1?x=&lt;bad&gt;">原文</a>', caption)

    def test_chunk_media_uses_size_ten(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = [
                DownloadedMedia(MediaItem(f"https://x/{i}.jpg", MediaType.IMAGE), Path(tmp) / f"{i}.jpg", 1)
                for i in range(11)
            ]

            chunks = chunk_media(media)

        self.assertEqual([len(chunk) for chunk in chunks], [10, 1])

    async def test_single_photo_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.jpg"
            path.write_bytes(b"x")
            media = [DownloadedMedia(MediaItem("https://x/a.jpg", MediaType.IMAGE), path, 1)]
            bot = FakeBot()
            publisher = TelegramPublisher(bot, "@c")

            result = await publisher.publish_note(sample_note(), media)

        self.assertEqual(result.status, PublishStatus.SENT)
        self.assertEqual(bot.calls[0][0], "photo")

    async def test_media_failure_degrades_to_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.jpg"
            path.write_bytes(b"x")
            media = [DownloadedMedia(MediaItem("https://x/a.jpg", MediaType.IMAGE), path, 1)]
            bot = FakeBot()
            bot.fail_media = True
            publisher = TelegramPublisher(bot, "@c", retries=2)

            result = await publisher.publish_note(sample_note(), media)

        self.assertEqual(result.status, PublishStatus.SENT_DEGRADED)
        self.assertEqual(bot.calls[-1][0], "message")

    async def test_retry_after_waits_and_retries_media_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = []
            for index in range(2):
                path = Path(tmp) / f"{index}.jpg"
                path.write_bytes(b"x")
                media.append(DownloadedMedia(MediaItem(f"https://x/{index}.jpg", MediaType.IMAGE), path, 1))
            bot = FakeBot()
            bot.retry_after_group_failures = 1
            publisher = TelegramPublisher(bot, "@c", retry_after_padding_seconds=1.0)

            with (
                patch("rednote2tg.telegram_publisher.asyncio.sleep", new_callable=AsyncMock) as sleep,
                self.assertLogs("rednote2tg.telegram_publisher", level="WARNING") as logs,
            ):
                result = await publisher.publish_note(sample_note(), media)

        self.assertEqual(result.status, PublishStatus.SENT)
        self.assertEqual(bot.group_attempts, 2)
        sleep.assert_awaited_once_with(31.0)
        self.assertIn("telegram flood limit hit", logs.output[0])
        self.assertIn("retry_after=30", logs.output[0])
        self.assertFalse(any(call[0] == "message" for call in bot.calls))

    async def test_retry_after_does_not_degrade_to_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = []
            for index in range(2):
                path = Path(tmp) / f"{index}.jpg"
                path.write_bytes(b"x")
                media.append(DownloadedMedia(MediaItem(f"https://x/{index}.jpg", MediaType.IMAGE), path, 1))
            bot = FakeBot()
            bot.retry_after_group_failures = 3
            publisher = TelegramPublisher(bot, "@c", retries=2, retry_after_padding_seconds=1.0)

            with patch("rednote2tg.telegram_publisher.asyncio.sleep", new_callable=AsyncMock) as sleep:
                result = await publisher.publish_note(sample_note(), media)

        self.assertEqual(result.status, PublishStatus.FAILED)
        self.assertEqual(bot.group_attempts, 3)
        self.assertEqual(sleep.await_count, 2)
        self.assertFalse(any(call[0] == "message" for call in bot.calls))

    async def test_multi_media_later_groups_have_no_caption(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = []
            for index in range(11):
                path = Path(tmp) / f"{index}.jpg"
                path.write_bytes(b"x")
                media.append(DownloadedMedia(MediaItem(f"https://x/{index}.jpg", MediaType.IMAGE), path, 1))
            bot = FakeBot()
            publisher = TelegramPublisher(bot, "@c")

            with patch("rednote2tg.telegram_publisher.asyncio.sleep", new_callable=AsyncMock) as sleep:
                result = await publisher.publish_note(sample_note(), media)

        self.assertEqual(result.status, PublishStatus.SENT)
        first_group = bot.calls[0][2]
        second_group = bot.calls[1][2]
        sleep.assert_awaited_once_with(1)
        
        first_caption = first_group[0].caption if hasattr(first_group[0], "caption") else first_group[0].get("caption")
        second_caption = second_group[0].caption if hasattr(second_group[0], "caption") else second_group[0].get("caption")
        
        self.assertIsNotNone(first_caption)
        self.assertIsNone(second_caption)


if __name__ == "__main__":
    unittest.main()
