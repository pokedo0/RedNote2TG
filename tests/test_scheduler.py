import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.test_config_models import base_config
from rednote2tg.config import parse_config
from rednote2tg.db import NoteStore
from rednote2tg.models import Note, PublishResult, PublishStatus, SourceRef
from rednote2tg.scheduler import PublishJobRunner, handle_run_once, handle_status, is_authorized, register_schedules


class FakeSource:
    def __init__(self, notes, keyword_query=None):
        self.notes = notes
        self.last_keyword_query = keyword_query

    def collect(self):
        return list(self.notes), []


class FakeDownloader:
    def __init__(self):
        self.cleaned = False
        self.upload_live_photo = None

    async def download_all(self, note_id, media, upload_live_photo=True):
        self.upload_live_photo = upload_live_photo
        return []

    def cleanup(self):
        self.cleaned = True


class FakePublisher:
    async def publish_note(self, note, media):
        return PublishResult(PublishStatus.SENT_DEGRADED, (100,))


class SequencePublisher:
    def __init__(self, results):
        self.results = list(results)

    async def publish_note(self, note, media):
        return self.results.pop(0)


class FakeScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append((func, trigger, kwargs))

    def get_jobs(self):
        return self.jobs


class FakeMessage:
    def __init__(self, user_id):
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)


def note(note_id):
    return Note(note_id=note_id, url=f"https://xhs/{note_id}", title=note_id, source=SourceRef("keyword", "k"))


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
            self.assertIn("elapsed_seconds", result)
            self.assertEqual(result["source_collected_notes"], 2)
            self.assertEqual(result["source_collected_errors"], 0)
            self.assertEqual(result["keyword_query"], "")
            self.assertEqual(result["keyword_time_filter"], "-")
            self.assertEqual(second_result["published"], 1)
            self.assertTrue(store.is_active("n1"))
            self.assertTrue(store.is_active("n2"))
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

    def test_register_schedules_adds_one_job_per_time(self):
        config = parse_config(base_config())
        scheduler = FakeScheduler()
        runner = SimpleNamespace(run_once=lambda: None)

        register_schedules(scheduler, config, runner)

        self.assertEqual(len(scheduler.jobs), 2)
        self.assertEqual(scheduler.jobs[0][2]["id"], "publish-09:00")

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
            self.assertIn("\n  publish published=1 skipped=0 failed=0 source_errors=0", authorized.answers[0])
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

            await handle_status(message, store, scheduler, ())

            self.assertIn("status:", message.answers[0])
            store.close()


if __name__ == "__main__":
    unittest.main()
