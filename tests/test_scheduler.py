import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import yaml

from tests.test_config_models import base_config
from tests.test_keyword_rules import base_rules
from rednote2tg.config import load_config, parse_config
from rednote2tg.db import NoteStore
from rednote2tg.models import (
    DownloadedMedia,
    FilteredNote,
    MediaItem,
    MediaType,
    Note,
    PublishResult,
    PublishStatus,
    SourceRef,
)
from rednote2tg.scheduler import (
    PublishJobRunner,
    RuntimeState,
    SourceErrorAutoPause,
    extract_xhs_url,
    format_run_once_summary,
    handle_fetch_note,
    handle_ping,
    handle_reload,
    handle_run_once,
    handle_runtime_run_once,
    handle_status,
    handle_update_cookie,
    is_authorized,
    register_schedules,
)
from rednote2tg.xhs_source import CollectionBatch, XhsSource


class FakeSource:
    def __init__(self, notes, keyword_query=None, keyword_rule_name="", merged_cookies=None):
        self.notes = notes
        self.last_keyword_query = keyword_query
        self.last_keyword_rule_name = keyword_rule_name
        self.fetched_urls = []
        self.active_note_ids = None
        self.detail_limit = None
        self.collect_calls = []
        self.merged_cookies = merged_cookies

    def collect(self, active_note_ids=None, detail_limit=None, on_note=None):
        self.active_note_ids = active_note_ids
        self.detail_limit = detail_limit
        active_note_ids = active_note_ids or set()
        self.collect_calls.append((set(active_note_ids), detail_limit))
        notes = [item for item in self.notes if item.note_id not in active_note_ids]
        if detail_limit is not None:
            notes = notes[:detail_limit]
        if on_note is not None:
            for item in notes:
                on_note(item)
        return notes, []

    def fetch_note_url(self, url):
        self.fetched_urls.append(url)
        if self.notes:
            return self.notes[0]
        return None

    def merged_cookie_header(self):
        return self.merged_cookies

    def replace_client(self, client, *, owned=True):
        self.client = client


class BlockingSource(FakeSource):
    def __init__(self, notes):
        super().__init__(notes)
        self.first_detail_ready = Event()
        self.second_detail_ready = Event()
        self.release_second_detail = Event()

    def collect(self, active_note_ids=None, detail_limit=None, on_note=None):
        self.active_note_ids = active_note_ids
        self.detail_limit = detail_limit
        active_note_ids = active_note_ids or set()
        notes = [item for item in self.notes if item.note_id not in active_note_ids]
        notes = notes[:detail_limit] if detail_limit is not None else notes
        if on_note is not None and notes:
            on_note(notes[0])
            self.first_detail_ready.set()
            self.release_second_detail.wait(timeout=2)
            for item in notes[1:]:
                on_note(item)
            self.second_detail_ready.set()
        return notes, []


class PagedCollection:
    def __init__(self, source, active_note_ids):
        self.source = source
        self.active_note_ids = set(active_note_ids)
        self.page = 0
        self.remaining = []

    def collect_next(self, active_note_ids=None, batch_size=None, on_note=None):
        if active_note_ids is not None:
            self.active_note_ids = set(active_note_ids)
        if not self.remaining:
            page_index = self.page
            self.page += 1
            if page_index >= len(self.source.pages):
                return CollectionBatch((), (), True)
            self.source.page_calls.append((page_index + 1, set(self.active_note_ids)))
            self.remaining = [
                item for item in self.source.pages[page_index]
                if item.note_id not in self.active_note_ids
            ]
        notes = self.remaining
        if batch_size is not None:
            notes = notes[:batch_size]
        self.remaining = self.remaining[len(notes):]
        if on_note is not None:
            for item in notes:
                on_note(item)
        return CollectionBatch(
            tuple(notes),
            (),
            not self.remaining and self.page >= len(self.source.pages),
        )


class PagedSource(FakeSource):
    def __init__(self, pages):
        super().__init__([item for page in pages for item in page])
        self.pages = pages
        self.page_calls = []

    def start_collection(self, active_note_ids=None):
        return PagedCollection(self, active_note_ids or set())


class FilteredPagedCollection:
    def __init__(self, source, active_note_ids):
        self.source = source
        self.active_note_ids = set(active_note_ids)
        self.page = 0

    def collect_next(self, active_note_ids=None, batch_size=None, on_note=None):
        if active_note_ids is not None:
            self.active_note_ids = set(active_note_ids)
        self.source.page_calls.append((self.page + 1, set(self.active_note_ids)))
        if self.page == 0:
            self.page += 1
            filtered = FilteredNote(
                note_id="filtered-1",
                url="https://xhs/filtered-1",
                title="filtered-1",
                source=SourceRef("keyword", "k"),
                liked_count=0,
                collected_count=0,
                comment_count=0,
                share_count=0,
                reason="low_interaction: liked=0, collected=0, comment=0, shared=0",
            )
            notes = (note("page-1"),)
            if on_note is not None:
                on_note(notes[0])
            return CollectionBatch(notes, (), False, (filtered,))
        self.page += 1
        notes = (note("page-2"),)
        if on_note is not None:
            on_note(notes[0])
        return CollectionBatch(notes, (), True)


class FilteredPagedSource(FakeSource):
    def __init__(self):
        super().__init__([])
        self.page_calls = []

    def start_collection(self, active_note_ids=None):
        return FilteredPagedCollection(self, active_note_ids or set())


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
        return PublishResult(PublishStatus.SENT, (100,))

    async def send_debug_message(self, text):
        self.debug_messages.append(text)


class ObservingPublisher(FakePublisher):
    def __init__(self):
        super().__init__()
        self.first_published = Event()

    async def publish_note(self, note, media, chat_id=None):
        result = await super().publish_note(note, media, chat_id)
        if len(self.published) == 1:
            self.first_published.set()
        return result


class BlockingPublisher(FakePublisher):
    def __init__(self):
        super().__init__()
        self.first_publish_started = Event()
        self.release_first_publish = Event()

    async def publish_note(self, note, media, chat_id=None):
        result = await super().publish_note(note, media, chat_id)
        if len(self.published) == 1:
            self.first_publish_started.set()
            await asyncio.to_thread(self.release_first_publish.wait, 2)
        return result


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
    async def test_runner_stores_prefiltered_notes_before_next_page(self):
        data = base_config()
        data["publishing"]["notes_per_run"] = 2
        config = parse_config(data)
        source = FilteredPagedSource()
        publisher = FakePublisher()
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, source, store, FakeDownloader(), publisher)

            result = await runner.run_once()

            row = store.conn.execute(
                "SELECT status, error_message FROM published_notes WHERE note_id = ?",
                ("filtered-1",),
            ).fetchone()
            self.assertEqual(result["filtered"], 1)
            self.assertEqual(row["status"], PublishStatus.FILTERED.value)
            self.assertIn("liked=0", row["error_message"])
            self.assertIn("filtered-1", source.page_calls[1][1])
            self.assertEqual([item[0].note_id for item in publisher.published], ["page-1", "page-2"])
            store.close()

    async def test_update_cookie_swaps_client_after_persistence_and_closes_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.write_runtime_files(tmp)
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            old_client = SimpleNamespace(close=Mock(), merged_cookie_header=lambda: "fresh-cookie")
            new_client = SimpleNamespace(close=Mock(), merged_cookie_header=lambda: "fresh-cookie")
            with patch.object(XhsSource, "_create_client", return_value=old_client):
                source = XhsSource(config.xhs, config.sources)
            runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())
            message = FakeMessage(1, "/update_cookie fresh-cookie")

            with patch.object(XhsSource, "_create_client", return_value=new_client):
                await handle_update_cookie(message, runner, (1,), str(config_path))

            self.assertEqual(message.answers, ["✅ Cookie 已更新并生效"])
            self.assertIn("fresh-cookie", config_path.read_text(encoding="utf-8"))
            self.assertIs(source.client, new_client)
            self.assertEqual(runner.config.xhs.cookies, "fresh-cookie")
            old_client.close.assert_called_once_with()
            new_client.close.assert_not_called()
            source.close()
            new_client.close.assert_called_once_with()
            store.close()

    async def test_update_cookie_write_failure_keeps_old_client_and_closes_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.write_runtime_files(tmp)
            original = config_path.read_text(encoding="utf-8")
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            old_client = SimpleNamespace(close=Mock(), merged_cookie_header=lambda: "fresh-cookie")
            new_client = SimpleNamespace(close=Mock(), merged_cookie_header=lambda: "fresh-cookie")
            with patch.object(XhsSource, "_create_client", return_value=old_client):
                source = XhsSource(config.xhs, config.sources)
            runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())
            message = FakeMessage(1, "/update_cookie fresh-cookie")

            with (
                patch.object(XhsSource, "_create_client", return_value=new_client),
                patch.object(Path, "write_text", side_effect=PermissionError("read only")),
            ):
                await handle_update_cookie(message, runner, (1,), str(config_path))

            self.assertIn("当前 Cookie 仍在使用", message.answers[0])
            self.assertEqual(config_path.read_text(encoding="utf-8"), original)
            self.assertIs(source.client, old_client)
            self.assertEqual(runner.config.xhs.cookies, config.xhs.cookies)
            old_client.close.assert_not_called()
            new_client.close.assert_called_once_with()
            source.close()
            store.close()

    async def test_update_cookie_constructor_failure_keeps_config_and_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.write_runtime_files(tmp)
            original = config_path.read_text(encoding="utf-8")
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            old_client = SimpleNamespace(close=Mock(), merged_cookie_header=lambda: "fresh-cookie")
            with patch.object(XhsSource, "_create_client", return_value=old_client):
                source = XhsSource(config.xhs, config.sources)
            runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())
            message = FakeMessage(1, "/update_cookie fresh-cookie")

            with patch.object(XhsSource, "_create_client", side_effect=RuntimeError("bad client")):
                await handle_update_cookie(message, runner, (1,), str(config_path))

            self.assertIn("当前 Cookie 仍在使用", message.answers[0])
            self.assertEqual(config_path.read_text(encoding="utf-8"), original)
            self.assertIs(source.client, old_client)
            self.assertEqual(runner.config.xhs.cookies, config.xhs.cookies)
            old_client.close.assert_not_called()
            source.close()
            store.close()

    async def test_update_cookie_preserves_comments_and_uses_merged_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            data = base_config()
            original = yaml.safe_dump(data, allow_unicode=True).replace(
                "cookies: a1=test",
                "# credentials\n  cookies: \"a1=test\"",
            )
            config_path.write_text(original, encoding="utf-8")
            config = load_config(config_path)
            store = NoteStore(root / "db.sqlite")
            old_client = SimpleNamespace(close=Mock())
            new_client = SimpleNamespace(close=Mock(), merged_cookie_header=lambda: "loadts=123;xsecappid=xhs-pc-web")
            with patch.object(XhsSource, "_create_client", return_value=old_client):
                source = XhsSource(config.xhs, config.sources)
            runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())
            message = FakeMessage(1, "/update_cookie fresh-cookie")

            with patch.object(XhsSource, "_create_client", return_value=new_client):
                await handle_update_cookie(message, runner, (1,), str(config_path), self.runtime_state(config, store))

            self.assertEqual(message.answers, ["✅ Cookie 已更新并生效"])
            self.assertIn("# credentials", config_path.read_text(encoding="utf-8"))
            self.assertIn('cookies: "loadts=123;xsecappid=xhs-pc-web"', config_path.read_text(encoding="utf-8"))
            self.assertEqual(runner.config.xhs.cookies, "loadts=123;xsecappid=xhs-pc-web")
            source.close()
            store.close()

    async def test_runtime_run_once_persists_merged_cookies_after_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.write_runtime_files(tmp)
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            source = FakeSource([note("n1")], merged_cookies="loadts=123;xsecappid=xhs-pc-web")
            runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())
            state = RuntimeState(config, runner, store, None, config_path=str(config_path))

            result = await state.run_once()

            self.assertEqual(result["published"], 1)
            self.assertIn('cookies: "loadts=123;xsecappid=xhs-pc-web"', config_path.read_text(encoding="utf-8"))
            self.assertEqual(state.config.xhs.cookies, "loadts=123;xsecappid=xhs-pc-web")
            store.close()

    async def test_runtime_run_once_does_not_rewrite_unchanged_cookies(self):
        data = base_config()
        data["xhs"]["cookies"] = "b2=new;a1=test"
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.write_runtime_files(tmp, data)
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            source = FakeSource([note("n1")], merged_cookies="a1=test;b2=new")
            runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())
            state = RuntimeState(config, runner, store, None, config_path=str(config_path))
            before = config_path.read_text(encoding="utf-8")

            await state.run_once()

            self.assertEqual(config_path.read_text(encoding="utf-8"), before)
            store.close()

    async def test_runtime_run_once_cookie_write_failure_does_not_mask_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.write_runtime_files(tmp)
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            source = FakeSource([note("n1")], merged_cookies="loadts=123")
            runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())
            state = RuntimeState(config, runner, store, None, config_path=str(config_path))

            with patch("rednote2tg.scheduler._replace_config_cookie", side_effect=PermissionError("locked")):
                result = await state.run_once()

            self.assertEqual(result["published"], 1)
            self.assertIn("a1=test", config_path.read_text(encoding="utf-8"))
            store.close()

    def write_runtime_files(self, tmp, data=None, rules=None):
        root = Path(tmp)
        config_path = root / "config.yaml"
        rules_path = root / "keyword_rules.yaml"
        rules_path.write_text(yaml.safe_dump(rules or base_rules(), allow_unicode=True), encoding="utf-8")
        config_path.write_text(yaml.safe_dump(data or base_config(), allow_unicode=True), encoding="utf-8")
        return config_path

    def runtime_state(self, config, store, scheduler=None):
        source = FakeSource([note("n1")])
        source.sources_config = config.sources
        source.client = object()
        runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())
        return RuntimeState(config, runner, store, scheduler or FakeScheduler())

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
            self.assertEqual(result["source_collected_notes"], 1)
            self.assertEqual(result["source_collected_errors"], 0)
            self.assertEqual(result["keyword_query"], "")
            self.assertEqual(result["keyword_time_filter"], "-")
            self.assertEqual(result["telegram_retry_after_count"], 0)
            self.assertEqual(second_result["published"], 1)
            self.assertEqual(second_result["published_media"], 0)
            self.assertTrue(store.is_active("n1"))
            self.assertTrue(store.is_active("n2"))
            store.close()

    async def test_runner_passes_active_ids_to_source_before_collecting(self):
        config = parse_config(base_config())
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            store.record_publish(note("existing"), PublishResult(PublishStatus.SENT), ttl_days=7)
            source = FakeSource([note("new")])
            runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())

            await runner.run_once()

            self.assertEqual(source.collect_calls[0][0], {"existing"})
            self.assertEqual(source.detail_limit, config.publishing.notes_per_run)
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
                        PublishResult(PublishStatus.SENT, (101,)),
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

    async def test_runner_waits_between_note_uploads_after_previous_note_finishes(self):
        data = base_config()
        data["publishing"]["note_interval_seconds"] = 2.5
        config = parse_config(data)
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(
                config,
                FakeSource([note("n1"), note("n2"), note("n3")]),
                store,
                FakeDownloader(),
                SequencePublisher(
                    [
                        PublishResult(PublishStatus.SENT, (101,)),
                        PublishResult(PublishStatus.SENT, (102,)),
                        PublishResult(PublishStatus.SENT, (103,)),
                    ]
                ),
            )

            with patch("rednote2tg.scheduler.asyncio.sleep", new_callable=AsyncMock) as sleep:
                result = await runner.run_once()

            self.assertEqual(result["published"], 3)
            self.assertEqual([await_args.args[0] for await_args in sleep.await_args_list], [2.5, 2.5])
            store.close()

    async def test_runner_logs_error_message_for_degraded_success(self):
        config = parse_config(base_config())
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(
                config,
                FakeSource([note("n1")]),
                store,
                FakeDownloader(),
                SequencePublisher([PublishResult(PublishStatus.SENT_DEGRADED, (101,), "media bad")]),
            )

            with self.assertLogs("rednote2tg.scheduler", level="INFO") as logs:
                await runner.run_once()

            self.assertTrue(any("error_message=media bad" in line for line in logs.output))
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
                        PublishResult(PublishStatus.SENT, (101,)),
                    ]
                ),
            )

            first_result = await runner.run_once()

            self.assertEqual(first_result["failed"], 1)
            self.assertTrue(store.is_active("n1"))
            second_result = await runner.run_once()
            self.assertEqual(second_result["published"], 0)
            self.assertTrue(store.is_active("n1"))
            store.close()

    async def test_runner_waits_retry_after_before_next_note_after_flood_failure(self):
        config = parse_config(base_config())
        publisher = SequencePublisher(
            [
                PublishResult(PublishStatus.FAILED, error_message="flood", retry_after_seconds=44),
                PublishResult(PublishStatus.SENT, (101,)),
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

    async def test_runner_adds_retry_after_wait_after_normal_note_interval(self):
        data = base_config()
        data["publishing"]["note_interval_seconds"] = 2.0
        config = parse_config(data)
        publisher = SequencePublisher(
            [
                PublishResult(PublishStatus.FAILED, error_message="flood", retry_after_seconds=4),
                PublishResult(PublishStatus.SENT, (101,)),
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

            self.assertEqual([await_args.args[0] for await_args in sleep.await_args_list], [2.0, 5.0])
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["published"], 1)
            store.close()

    async def test_runner_fetches_next_batch_until_success_target_is_reached(self):
        data = base_config()
        data["publishing"]["notes_per_run"] = 2
        config = parse_config(data)
        publisher = SequencePublisher(
            [
                PublishResult(PublishStatus.FAILED, error_message="bad"),
                PublishResult(PublishStatus.SENT, (101,)),
                PublishResult(PublishStatus.SENT, (102,)),
            ]
        )
        source = PagedSource([[note("n1"), note("n2")], [note("n3")]])
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, source, store, FakeDownloader(), publisher)

            result = await runner.run_once()

            self.assertEqual(result["published"], 2)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["source_collected_notes"], 3)
            self.assertEqual([call[0] for call in source.page_calls], [1, 2])
            self.assertEqual(source.page_calls[1][1], {"n1", "n2"})
            self.assertTrue(store.is_active("n1"))
            self.assertTrue(store.is_active("n2"))
            self.assertTrue(store.is_active("n3"))
            store.close()

    async def test_runner_does_not_request_next_page_until_current_page_is_consumed(self):
        data = base_config()
        data["publishing"]["notes_per_run"] = 2
        config = parse_config(data)
        source = PagedSource([[note("n1"), note("n2")], [note("n3")]])
        publisher = BlockingPublisher()
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, source, store, FakeDownloader(), publisher)
            run_task = asyncio.create_task(runner.run_once())

            try:
                self.assertTrue(await asyncio.to_thread(publisher.first_publish_started.wait, 1))
                self.assertEqual([call[0] for call in source.page_calls], [1])
            finally:
                publisher.release_first_publish.set()

            result = await run_task

            self.assertEqual(result["published"], 2)
            self.assertEqual([call[0] for call in source.page_calls], [1])
            store.close()

    async def test_runner_consumes_current_page_in_multiple_batches_before_paging(self):
        data = base_config()
        data["publishing"]["notes_per_run"] = 2
        config = parse_config(data)
        source = PagedSource(
            [[note("n1"), note("n2"), note("n3"), note("n4")], [note("n5")]]
        )
        publisher = SequencePublisher(
            [
                PublishResult(PublishStatus.FAILED, error_message="bad"),
                PublishResult(PublishStatus.SENT, (101,)),
                PublishResult(PublishStatus.SENT, (102,)),
                PublishResult(PublishStatus.SENT, (103,)),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, source, store, FakeDownloader(), publisher)

            result = await runner.run_once()

            self.assertEqual(result["published"], 3)
            self.assertEqual([call[0] for call in source.page_calls], [1])
            store.close()

    async def test_runner_attempts_all_notes_from_current_page_after_target(self):
        data = base_config()
        data["publishing"]["notes_per_run"] = 1
        config = parse_config(data)
        source = PagedSource([[note("n1"), note("n2"), note("n3")], [note("n4")]])
        publisher = FakePublisher()
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, source, store, FakeDownloader(), publisher)

            result = await runner.run_once()

            self.assertEqual(result["published"], 1)
            self.assertEqual([call[0] for call in source.page_calls], [1])
            self.assertEqual([item[0].note_id for item in publisher.published], ["n1"])
            store.close()

    async def test_runner_uploads_first_ready_note_before_detail_batch_finishes(self):
        data = base_config()
        data["publishing"]["notes_per_run"] = 2
        config = parse_config(data)
        source = BlockingSource([note("n1"), note("n2")])
        publisher = ObservingPublisher()
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, source, store, FakeDownloader(), publisher)
            run_task = asyncio.create_task(runner.run_once())

            try:
                self.assertTrue(await asyncio.to_thread(source.first_detail_ready.wait, 1))
                self.assertTrue(await asyncio.to_thread(publisher.first_published.wait, 1))
                self.assertFalse(source.second_detail_ready.is_set())
            finally:
                source.release_second_detail.set()

            result = await run_task

            self.assertEqual(result["published"], 2)
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
            self.assertIn("keyword rule=- query=- time_filter=-", authorized.answers[0])
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

            self.assertIn("keyword rule=- query=凉鞋 水晶 白色 time_filter=一周内", message.answers[0])
            self.assertNotIn("keyword_note_time", message.answers[0])
            store.close()

    async def test_run_once_reports_selected_keyword_rule(self):
        config = parse_config(base_config())
        keyword_query = SimpleNamespace(query="凉鞋 水晶 白色", note_time=2)
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(
                config,
                FakeSource([note("n1")], keyword_query=keyword_query, keyword_rule_name="B"),
                store,
                FakeDownloader(),
                FakePublisher(),
            )

            result = await runner.run_once()

            self.assertEqual(result["keyword_rule"], "B")
            self.assertIn("keyword rule=B query=凉鞋 水晶 白色", format_run_once_summary(result))
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

    async def test_reload_command_checks_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.write_runtime_files(tmp)
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            state = self.runtime_state(config, store)
            state.config_path = str(config_path)
            message = FakeMessage(9)

            await handle_reload(message, state)

            self.assertEqual(message.answers, ["unauthorized"])
            store.close()

    async def test_reload_updates_supported_runtime_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = base_config()
            config_path = self.write_runtime_files(tmp, data)
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            scheduler = FakeScheduler()
            state = self.runtime_state(config, store, scheduler)
            state.config_path = str(config_path)
            register_schedules(scheduler, config, state)

            data["xhs"] = {"cookies": "fresh-cookie", "proxies": {"http": "http://proxy"}}
            data["sources"]["keywords"]["search_limit_per_query"] = 7
            data["sources"]["homefeed"] = {
                "enabled": True,
                "categories": ["homefeed_recommend"],
                "limit_per_category": 5,
            }
            data["publishing"]["notes_per_run"] = 7
            data["publishing"]["telegram_retry_after_padding_seconds"] = 10
            data["publishing"]["upload_live_photo"] = False
            data["dedup"]["ttl_days"] = 10
            data["schedule"]["interval_minutes"] = 50
            config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            new_client = object()
            message = FakeMessage(1)

            with patch("rednote2tg.scheduler.XhsSource._create_client", return_value=new_client) as create_client:
                await handle_reload(message, state)

            self.assertIn("配置已热加载", message.answers[0])
            self.assertEqual(state.config.xhs.cookies, "fresh-cookie")
            self.assertEqual(state.runner.config.publishing.notes_per_run, 7)
            self.assertFalse(state.runner.config.publishing.upload_live_photo)
            self.assertEqual(state.runner.config.dedup.ttl_days, 10)
            self.assertEqual(state.runner.source.sources_config.keywords.search_limit_per_query, 7)
            self.assertTrue(state.runner.source.sources_config.homefeed.enabled)
            self.assertEqual(state.runner.publisher.retry_after_padding_seconds, 10)
            self.assertIs(state.runner.source.client, new_client)
            create_client.assert_called_once_with(state.config.xhs)
            job_ids = [job[2]["id"] for job in scheduler.jobs]
            self.assertNotIn("publish-02:00", job_ids)
            self.assertEqual(len(job_ids), len(set(job_ids)))
            store.close()

    async def test_reload_preserves_paused_scheduler_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = base_config()
            config_path = self.write_runtime_files(tmp, data)
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            scheduler = FakeScheduler()
            state = self.runtime_state(config, store, scheduler)
            state.config_path = str(config_path)
            register_schedules(scheduler, config, state)
            scheduler.pause()
            data["schedule"]["interval_minutes"] = 50
            config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

            await handle_reload(FakeMessage(1), state)

            self.assertTrue(scheduler.paused)
            store.close()

    async def test_reload_persists_merged_cookies_after_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = base_config()
            config_path = self.write_runtime_files(tmp, data)
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            state = self.runtime_state(config, store)
            state.config_path = str(config_path)
            state.runner.source.merged_cookies = "loadts=123;xsecappid=xhs-pc-web"

            message = FakeMessage(1)
            await handle_reload(message, state)

            self.assertIn("配置已热加载", message.answers[0])
            self.assertIn('cookies: "loadts=123;xsecappid=xhs-pc-web"', config_path.read_text(encoding="utf-8"))
            self.assertEqual(state.config.xhs.cookies, "loadts=123;xsecappid=xhs-pc-web")
            store.close()

    async def test_reload_rejects_invalid_keyword_rules_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = base_config()
            config_path = self.write_runtime_files(tmp, data)
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            state = self.runtime_state(config, store)
            state.config_path = str(config_path)
            bad_rules = {"length_weights": {3: 1.0}}
            self.write_runtime_files(tmp, data, rules=bad_rules)
            data["publishing"]["notes_per_run"] = 7
            config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            message = FakeMessage(1)

            await handle_reload(message, state)

            self.assertIn("热加载失败", message.answers[0])
            self.assertEqual(state.runner.config.publishing.notes_per_run, 3)
            store.close()

    async def test_reload_rejects_invalid_weighted_keyword_rules_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = base_config()
            config_path = self.write_runtime_files(tmp, data)
            config = load_config(config_path)
            store = NoteStore(root / "db.sqlite")
            state = self.runtime_state(config, store)
            state.config_path = str(config_path)

            valid_rules_path = root / "keyword_rules_A.yaml"
            bad_rules_path = root / "keyword_rules_B.yaml"
            valid_rules_path.write_text(yaml.safe_dump(base_rules(), allow_unicode=True), encoding="utf-8")
            bad_rules_path.write_text(yaml.safe_dump({"length_weights": {3: 1.0}}, allow_unicode=True), encoding="utf-8")
            data["sources"]["keywords"] = {
                "enabled": True,
                "rules": [
                    {"name": "A", "weight": 0.7, "rules_path": "keyword_rules_A.yaml"},
                    {"name": "B", "weight": 0.3, "rules_path": "keyword_rules_B.yaml"},
                ],
                "search_limit_per_query": 20,
            }
            data["publishing"]["notes_per_run"] = 7
            config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            message = FakeMessage(1)

            await handle_reload(message, state)

            self.assertIn("热加载失败", message.answers[0])
            self.assertEqual(state.runner.config.publishing.notes_per_run, 3)
            self.assertEqual(state.config.sources.keywords.rules_path, str(root / "keyword_rules.yaml"))
            store.close()

    async def test_reload_rejects_restart_only_config_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = base_config()
            config_path = self.write_runtime_files(tmp, data)
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            state = self.runtime_state(config, store)
            state.config_path = str(config_path)
            data["telegram"]["channel_id"] = "@other"
            data["publishing"]["notes_per_run"] = 7
            config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            message = FakeMessage(1)

            await handle_reload(message, state)

            self.assertIn("telegram.channel_id", message.answers[0])
            self.assertEqual(state.config.telegram.channel_id, "@channel")
            self.assertEqual(state.runner.config.publishing.notes_per_run, 3)
            store.close()

    async def test_reload_rejects_invalid_config_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = base_config()
            config_path = self.write_runtime_files(tmp, data)
            config = load_config(config_path)
            store = NoteStore(Path(tmp) / "db.sqlite")
            state = self.runtime_state(config, store)
            state.config_path = str(config_path)
            data["publishing"]["notes_per_run"] = 99
            config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            message = FakeMessage(1)

            await handle_reload(message, state)

            self.assertIn("热加载失败", message.answers[0])
            self.assertEqual(state.runner.config.publishing.notes_per_run, 3)
            store.close()

    async def test_runtime_run_once_waits_for_reload_lock(self):
        config = parse_config(base_config())
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            state = self.runtime_state(config, store)
            message = FakeMessage(1)

            async with state.lock:
                task = asyncio.create_task(handle_runtime_run_once(message, state))
                await asyncio.sleep(0)
                self.assertEqual(message.answers, [])

            await task

            self.assertIn("run_once done", message.answers[0])
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
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("xhs:\n  cookies: a1=test\n", encoding="utf-8")
            downloader = FakeDownloader()
            media_path = Path(tmp) / "downloaded.jpg"
            media_path.write_bytes(b"x")
            downloaded_media = DownloadedMedia(fetched_note.media[0], media_path, 1)
            downloader.downloads = [downloaded_media]
            publisher = FakePublisher()
            runner = PublishJobRunner(config, source, store, downloader, publisher)
            source.merged_cookies = "loadts=123;xsecappid=xhs-pc-web"
            message = FakeMessage(
                1,
                "/note https://www.xiaohongshu.com/explore/n1?xsec_token=abc",
                "private",
            )

            state = RuntimeState(config, runner, store, None, config_path=str(config_path))
            await handle_fetch_note(message, state)

            self.assertEqual(source.fetched_urls, ["https://www.xiaohongshu.com/explore/n1?xsec_token=abc"])
            self.assertTrue(downloader.cleaned)
            self.assertEqual(downloader.upload_live_photo, config.publishing.upload_live_photo)
            self.assertEqual(publisher.published, [(fetched_note, [downloaded_media], 1)])
            self.assertEqual(message.answers, [])
            self.assertIn('cookies: "loadts=123;xsecappid=xhs-pc-web"', config_path.read_text(encoding="utf-8"))
            store.close()

    async def test_note_command_requires_private_chat(self):
        config = parse_config(base_config())
        source = FakeSource([note("n1")])
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            config_path = Path(tmp) / "config.yaml"
            original = "xhs:\n  cookies: a1=test\n"
            config_path.write_text(original, encoding="utf-8")
            source.merged_cookies = "loadts=123"
            runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())
            message = FakeMessage(1, "/note https://www.xiaohongshu.com/explore/n1", "group")

            state = RuntimeState(config, runner, store, None, config_path=str(config_path))
            await handle_fetch_note(message, state)

            self.assertEqual(message.answers, ["请私聊发送 /note <小红书笔记链接>"])
            self.assertEqual(source.fetched_urls, [])
            self.assertEqual(config_path.read_text(encoding="utf-8"), original)
            store.close()

    async def test_ping_failure_still_persists_merged_cookies(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("xhs:\n  cookies: a1=test\n", encoding="utf-8")
            config = parse_config(base_config())
            store = NoteStore(Path(tmp) / "db.sqlite")
            source = FakeSource([note("n1")])
            source.client = SimpleNamespace(unread_message=Mock(side_effect=RuntimeError("expired")))
            source.merged_cookies = "loadts=123;xsecappid=xhs-pc-web"
            runner = PublishJobRunner(config, source, store, FakeDownloader(), FakePublisher())
            state = RuntimeState(config, runner, store, None, config_path=str(config_path))
            message = FakeMessage(1)

            await handle_ping(message, state)

            self.assertIn("❌ Cookie 已失效或请求异常", message.answers[0])
            self.assertIn('cookies: "loadts=123;xsecappid=xhs-pc-web"', config_path.read_text(encoding="utf-8"))
            store.close()


if __name__ == "__main__":
    unittest.main()
