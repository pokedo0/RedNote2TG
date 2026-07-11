import tempfile
import unittest
from pathlib import Path

from tests.test_config_models import base_config
from rednote2tg.config import parse_config
from rednote2tg.db import NoteStore
from rednote2tg.models import Note, PublishResult, PublishStatus, SourceRef
from rednote2tg.scheduler import PublishJobRunner


class DrySource:
    def collect(self, active_note_ids=None):
        return [Note("n1", "https://xhs/n1", "Title", source=SourceRef("keyword", "榴莲"))], []


class DryDownloader:
    async def download_all(self, note_id, media, upload_live_photo=True):
        return []

    def cleanup(self):
        pass


class DryPublisher:
    async def publish_note(self, note, media):
        return PublishResult(PublishStatus.SENT_DEGRADED, (1,))


class DryRunIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_keyword_to_publish_job_without_real_network(self):
        config = parse_config(base_config())
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            runner = PublishJobRunner(config, DrySource(), store, DryDownloader(), DryPublisher())

            result = await runner.run_once()

            self.assertEqual(result["published"], 1)
            self.assertTrue(store.is_active("n1"))
            store.close()


if __name__ == "__main__":
    unittest.main()
