import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_config_models import base_config
from rednote2tg.config import parse_config
from rednote2tg.db import NoteStore
from rednote2tg.models import DownloadedMedia, MediaItem, MediaType, Note, PublishResult, PublishStatus, SourceRef
from rednote2tg.scheduler import (
    PublishJobRunner,
    SourceErrorAutoPause,
    extract_xhs_url,
    format_run_once_summary,
    handle_fetch_note,
    handle_run_once,
    handle_status,
    is_authorized,
    register_schedules,
)


class FakeSource:
    def __init__(self, notes, keyword_query=None):
        self.notes = notes
        self.last_keyword_query = keyword_query
        self.fetched_urls = []

    def collect(self):
        return list(self.notes), []

    def fetch_note_url(self, url):
        self.fetched_urls.append(url)
        if self.notes:
            return self.notes[0]
        return None


class FakeDownloader:
    def __init__(self):
        self.cleaned = False
        self.upload_live_photo = None
        self.downloads = []

    async def download_all(self, note_id, media, upload_live_photo=True):
        self.upload_live_photo = upload_live_photo
        return self.downloads

    def cleanup(self):
        self.cleaned = True


class FakePublisher:
    def __init__(self):
        self.debug_messages = []
        self.telegram_retry_after_count = 0
        self.published = []

    async def publish_note(self, note, media, chat_id=None):
        self.published.append((note, media, chat_id))
        return PublishResult(PublishStatus.SENT_DEGRADED, (100,))

    async def send_debug_message(self, text):
        self.debug_messages.append(text)


class SequencePublisher:
    def __init__(self, results):
        self.results = list(results)
        self.telegram_retry_after_count = 0
        self.retry_after_padding_seconds = 0.0

    async def publish_note(self, note, media):
        return self.results.pop(0)


class FakeScheduler:
    def __init__(self):
        self.jobs = []
        self.paused = False
        self.state = 1

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append((func, trigger, kwargs))

    def get_jobs(self):
        return self.jobs

    def pause(self):
        self.paused = True
        self.state = 2

    def resume(self):
        self.paused = False
        self.state = 1


class SummaryRunner:
    def __init__(self, summaries, config=None, publisher=None):
        self.summaries = list(summaries)
        self.config = config
        self.publisher = publisher

    async def run_once(self):
        return self.summaries.pop(0)


class FakeMessage:
    def __init__(self, user_id, text="", chat_type="private"):
        self.from_user = SimpleNamespace(id=user_id)
        self.text = text
        self.chat = SimpleNamespace(id=user_id, type=chat_type)
        self.answers = []
        self.answer_kwargs = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        self.answer_kwargs.append(kwargs)


def note(note_id, media_count=0):
    media = tuple(MediaItem(f"https://img/{note_id}-{index}.jpg", MediaType.IMAGE) for index in range(media_count))
    return Note(note_id=note_id, url=f"https://xhs/{note_id}", title=note_id, source=SourceRef("keyword", "k"), media=media)


class SchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_runner_respects_notes_per_run_and_dedup(self):
        data = base_config()
        data["publishing"]["notes_per_run"] = 1
        config = parse_config(data)
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(
                config,
                FakeSource([note("n1"), note("n2")]),
                store,
                FakeDownloader(),
                FakePublisher(),
            )

            result = await runner.run_once()
            second_result = await runner.run_once()

            self.assertEqual(result["published"], 1)
            self.assertEqual(result["published_media"], 0)
            self.assertIn("elapsed_seconds", result)
            self.assertEqual(result["source_collected_notes"], 2)
            self.assertEqual(result["source_collected_errors"], 0)
            self.assertEqual(result["keyword_query"], "")
            self.assertEqual(result["keyword_time_filter"], "-")
            self.assertEqual(result["telegram_retry_after_count"], 0)
            self.assertEqual(second_result["published"], 1)
            self.assertEqual(second_result["published_media"], 0)
            self.assertTrue(store.is_active("n1"))
            self.assertTrue(store.is_active("n2"))
            store.close()

    async def test_runner_reports_published_and_failed_media_counts(self):
        config = parse_config(base_config())
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(
                config,
                FakeSource([note("n1", media_count=2), note("n2", media_count=3)]),
                store,
                FakeDownloader(),
                SequencePublisher(
                    [
                        PublishResult(PublishStatus.SENT_DEGRADED, (101,)),
                        PublishResult(PublishStatus.FAILED, error_message="bad"),
                    ]
                ),
            )

            result = await runner.run_once()

            self.assertEqual(result["published"], 1)
            self.assertEqual(result["published_media"], 2)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["failed_media"], 3)
            self.assertIn("published=1(media=2)", format_run_once_summary(result))
            self.assertIn("failed=1(media=3)", format_run_once_summary(result))
            store.close()

    async def test_runner_reports_retry_after_count_delta(self):
        config = parse_config(base_config())
        publisher = FakePublisher()
        publisher.telegram_retry_after_count = 4
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, FakeSource([note("n1")]), store, FakeDownloader(), publisher)

            original_publish = publisher.publish_note

            async def publish_note(note, media):
                publisher.telegram_retry_after_count += 2
                return await original_publish(note, media)

            publisher.publish_note = publish_note
            result = await runner.run_once()

            self.assertEqual(result["telegram_retry_after_count"], 2)
            store.close()

    async def test_runner_sends_debug_summary_to_channel_after_published_note(self):
        data = base_config()
        data["debug"] = {"enabled": True}
        config = parse_config(data)
        publisher = FakePublisher()
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, FakeSource([note("n1")]), store, FakeDownloader(), publisher)

            await runner.run_once()

            self.assertEqual(len(publisher.debug_messages), 1)
            self.assertIn("run_once done:", publisher.debug_messages[0])
            self.assertIn("TelegramRetryAfter count=0", publisher.debug_messages[0])
            store.close()

    async def test_runner_skips_debug_summary_when_nothing_published(self):
        data = base_config()
        data["debug"] = {"enabled": True}
        config = parse_config(data)
        publisher = SequencePublisher([PublishResult(PublishStatus.FAILED, error_message="bad")])
        publisher.debug_messages = []

        async def send_debug_message(text):
            publisher.debug_messages.append(text)

        publisher.send_debug_message = send_debug_message
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, FakeSource([note("n1")]), store, FakeDownloader(), publisher)

            await runner.run_once()

            self.assertEqual(publisher.debug_messages, [])
            store.close()

    async def test_runner_retries_note_after_failed_publish(self):
        config = parse_config(base_config())
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(
                config,
                FakeSource([note("n1")]),
                store,
                FakeDownloader(),
                SequencePublisher(
                    [
                        PublishResult(PublishStatus.FAILED, error_message="bad"),
                        PublishResult(PublishStatus.SENT_DEGRADED, (101,)),
                    ]
                ),
            )

            first_result = await runner.run_once()

            self.assertEqual(first_result["failed"], 1)
            self.assertFalse(store.is_active("n1"))
            second_result = await runner.run_once()
            self.assertEqual(second_result["published"], 1)
            self.assertTrue(store.is_active("n1"))
            store.close()

    async def test_runner_waits_retry_after_before_next_note_after_flood_failure(self):
        config = parse_config(base_config())
        publisher = SequencePublisher(
            [
                PublishResult(PublishStatus.FAILED, error_message="flood", retry_after_seconds=44),
                PublishResult(PublishStatus.SENT_DEGRADED, (101,)),
            ]
        )
        publisher.retry_after_padding_seconds = 1.0
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(
                config,
                FakeSource([note("n1"), note("n2")]),
                store,
                FakeDownloader(),
                publisher,
            )

            with patch("rednote2tg.scheduler.asyncio.sleep", new_callable=AsyncMock) as sleep:
                result = await runner.run_once()

            sleep.assert_awaited_once_with(45.0)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["published"], 1)
            store.close()

    async def test_runner_passes_live_photo_upload_config_to_downloader(self):
        data = base_config()
        data["publishing"]["upload_live_photo"] = False
        config = parse_config(data)
        downloader = FakeDownloader()
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, FakeSource([note("n1")]), store, downloader, FakePublisher())

            await runner.run_once()

            self.assertFalse(downloader.upload_live_photo)
            store.close()

    def test_register_schedules_adds_interval_jobs_outside_quiet_window(self):
        config = parse_config(base_config())
        scheduler = FakeScheduler()
        runner = SimpleNamespace(run_once=lambda: None)

        register_schedules(scheduler, config, runner)

        job_ids = [job[2]["id"] for job in scheduler.jobs]
        self.assertEqual(len(scheduler.jobs), 18)
        self.assertIn("publish-02:00", job_ids)
        self.assertNotIn("publish-03:00", job_ids)
        self.assertIn("publish-09:00", job_ids)
        self.assertEqual(scheduler.jobs[0][2]["jitter"], 600)

    def test_register_schedules_skips_jobs_that_jitter_into_quiet_window(self):
        data = base_config()
        data["schedule"]["interval_minutes"] = 5
        data["schedule"]["jitter_minutes"] = 10
        config = parse_config(data)
        scheduler = FakeScheduler()
        runner = SimpleNamespace(run_once=lambda: None)

        register_schedules(scheduler, config, runner)

        job_ids = [job[2]["id"] for job in scheduler.jobs]
        self.assertIn("publish-02:45", job_ids)
        self.assertNotIn("publish-02:50", job_ids)
        self.assertNotIn("publish-02:55", job_ids)
        self.assertIn("publish-09:00", job_ids)

    async def test_source_error_auto_pause_pauses_after_three_consecutive_source_errors(self):
        scheduler = FakeScheduler()
        runner = SummaryRunner(
            [
                {"source_errors": 1},
                {"source_errors": 1},
                {"source_errors": 1},
            ]
        )
        guard = SourceErrorAutoPause(runner, scheduler)

        await guard.run_once()
        await guard.run_once()
        self.assertFalse(scheduler.paused)
        await guard.run_once()

        self.assertTrue(scheduler.paused)

    async def test_source_error_auto_pause_resets_after_successful_collect(self):
        scheduler = FakeScheduler()
        runner = SummaryRunner(
            [
                {"source_errors": 1},
                {"source_errors": 1},
                {"source_errors": 0},
                {"source_errors": 1},
            ]
        )
        guard = SourceErrorAutoPause(runner, scheduler)

        await guard.run_once()
        await guard.run_once()
        await guard.run_once()
        await guard.run_once()

        self.assertFalse(scheduler.paused)

    async def test_source_error_auto_pause_sends_debug_notification_when_enabled(self):
        data = base_config()
        data["debug"] = {"enabled": True}
        config = parse_config(data)
        publisher = FakePublisher()
        scheduler = FakeScheduler()
        runner = SummaryRunner(
            [
                {"source_errors": 1},
                {"source_errors": 1},
                {"source_errors": 1},
            ],
            config=config,
            publisher=publisher,
        )
        guard = SourceErrorAutoPause(runner, scheduler)

        await guard.run_once()
        await guard.run_once()
        await guard.run_once()

        self.assertTrue(scheduler.paused)
        self.assertEqual(len(publisher.debug_messages), 1)
        self.assertIn("定时爬取任务已自动暂停", publisher.debug_messages[0])
        self.assertIn("/start_tasks", publisher.debug_messages[0])

    async def test_source_error_auto_pause_skips_debug_notification_when_disabled(self):
        config = parse_config(base_config())
        publisher = FakePublisher()
        scheduler = FakeScheduler()
        runner = SummaryRunner(
            [
                {"source_errors": 1},
                {"source_errors": 1},
                {"source_errors": 1},
            ],
            config=config,
            publisher=publisher,
        )
        guard = SourceErrorAutoPause(runner, scheduler)

        await guard.run_once()
        await guard.run_once()
        await guard.run_once()

        self.assertTrue(scheduler.paused)
        self.assertEqual(publisher.debug_messages, [])

    def test_authorization(self):
        self.assertTrue(is_authorized(1, (1, 2)))
        self.assertFalse(is_authorized(3, (1, 2)))
        self.assertTrue(is_authorized(3, ()))

    async def test_run_once_command_checks_admin(self):
        data = base_config()
        data["telegram"]["admin_user_ids"] = [1]
        config = parse_config(data)
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, FakeSource([note("n1")]), store, FakeDownloader(), FakePublisher())
            unauthorized = FakeMessage(2)
            authorized = FakeMessage(1)

            await handle_run_once(unauthorized, runner, config.telegram.admin_user_ids)
            await handle_run_once(authorized, runner, config.telegram.admin_user_ids)

            self.assertEqual(unauthorized.answers, ["unauthorized"])
            self.assertIn("run_once done", authorized.answers[0])
            self.assertIn("\n  source_collected notes=1 errors=0", authorized.answers[0])
            self.assertIn("\n  publish published=1(media=0) skipped=0 failed=0(media=0) source_errors=0", authorized.answers[0])
            self.assertIn("elapsed=", authorized.answers[0])
            self.assertIn("keyword query=- time_filter=-", authorized.answers[0])
            self.assertNotIn("keyword_note_time", authorized.answers[0])
            store.close()

    async def test_run_once_reports_generated_keyword_query(self):
        config = parse_config(base_config())
        keyword_query = SimpleNamespace(query="凉鞋 水晶 白色", note_time=2)
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(
                config,
                FakeSource([note("n1")], keyword_query=keyword_query),
                store,
                FakeDownloader(),
                FakePublisher(),
            )

            result = await runner.run_once()

            self.assertEqual(result["keyword_query"], "凉鞋 水晶 白色")
            self.assertEqual(result["keyword_note_time"], 2)
            self.assertEqual(result["keyword_time_filter"], "一周内")
            store.close()

    async def test_run_once_command_reports_keyword_time_filter(self):
        config = parse_config(base_config())
        keyword_query = SimpleNamespace(query="凉鞋 水晶 白色", note_time=2)
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(
                config,
                FakeSource([note("n1")], keyword_query=keyword_query),
                store,
                FakeDownloader(),
                FakePublisher(),
            )
            message = FakeMessage(1)

            await handle_run_once(message, runner, ())

            self.assertIn("keyword query=凉鞋 水晶 白色 time_filter=一周内", message.answers[0])
            self.assertNotIn("keyword_note_time", message.answers[0])
            store.close()

    async def test_status_command_reports_summary(self):
        config = parse_config(base_config())
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            message = FakeMessage(1)
            scheduler = FakeScheduler()

            scheduler.jobs = [
                SimpleNamespace(next_run_time=datetime(2026, 7, 4, 1, 0, tzinfo=UTC)),
            ]

            await handle_status(message, store, scheduler, (), config)

            self.assertIn("status:", message.answers[0])
            self.assertIn("crawl=running", message.answers[0])
            self.assertIn("next_run=2026-07-04 09:00:00", message.answers[0])
            self.assertIn("schedule=interval interval=60m jitter=0-10m quiet=03:00-09:00", message.answers[0])
            store.close()

    async def test_status_command_reports_paused_crawl_status(self):
        config = parse_config(base_config())
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            message = FakeMessage(1)
            scheduler = FakeScheduler()
            scheduler.pause()

            await handle_status(message, store, scheduler, (), config)

            self.assertIn("crawl=paused", message.answers[0])
            store.close()

    async def test_status_command_reports_unconfigured_crawl_status(self):
        config = parse_config(base_config())
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            message = FakeMessage(1)

            await handle_status(message, store, None, (), config)

            self.assertIn("crawl=unconfigured", message.answers[0])
            store.close()

    def test_extract_xhs_url_from_command_text(self):
        url = (
            "https://www.xiaohongshu.com/explore/6937d509000000001d039d86"
            "?xsec_token=abc&xsec_source=pc_search"
        )

        self.assertEqual(extract_xhs_url(f"/note {url}。"), url)
        self.assertIsNone(extract_xhs_url("/note https://example.com/a"))

    async def test_note_command_fetches_single_note_in_private_chat(self):
        config = parse_config(base_config())
        fetched_note = Note(
            note_id="n1",
            url="https://www.xiaohongshu.com/explore/n1",
            title="标题",
            description="正文",
            author="作者",
            source=SourceRef("manual", "url"),
            media=(MediaItem("https://img/1.jpg", MediaType.IMAGE),),
        )
        source = FakeSource([fetched_note])
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            downloader = FakeDownloader()
            media_path = Path(tmp) / "downloaded.jpg"
            media_path.write_bytes(b"x")
            downloaded_media = DownloadedMedia(fetched_note.media[0], media_path, 1)
            downloader.downloads = [downloaded_media]
            publisher = FakePublisher()
            runner = PublishJobRunner(config, source, store, downloader, publisher)
            message = FakeMessage(
                1,
                "/note https://www.xiaohongshu.com/explore/n1?xsec_token=abc",
                "private",
            )

            await handle_fetch_note(message, runner, ())

            self.assertEqual(source.fetched_urls, ["https://www.xiaohongshu.com/explore/n1?xsec_token=abc"])
            self.assertTrue(downloader.cleaned)
            self.assertEqual(downloader.upload_live_photo, config.publishing.upload_live_photo)
            self.assertEqual(publisher.published, [(fetched_note, [downloaded_media], 1)])
            self.assertEqual(message.answers, [])
            store.close()

    async def test_note_command_requires_private_chat(self):
        config = parse_config(base_config())
        source = FakeSource([note("n1")])
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())
            message = FakeMessage(1, "/note https://www.xiaohongshu.com/explore/n1", "group")

            await handle_fetch_note(message, runner, ())

            self.assertEqual(message.answers, ["请私聊发送 /note <小红书笔记链接>"])
            self.assertEqual(source.fetched_urls, [])
            store.close()


if __name__ == "__main__":
    unittest.main()
