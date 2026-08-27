import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rednote2tg.db import NoteStore
from rednote2tg.models import Note, PublishResult, PublishStatus, SourceRef


def note(note_id="n1"):
    return Note(note_id=note_id, url=f"https://xhs/{note_id}", title="Title", source=SourceRef("keyword", "k"))


class NoteStoreTest(unittest.TestCase):
    def test_record_publish_creates_active_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            store.record_publish(note(), PublishResult(PublishStatus.SENT, (10,)), ttl_days=7)

            self.assertTrue(store.is_active("n1"))
            summary = store.summary()

            self.assertEqual(summary.active_dedup_count, 1)
            self.assertEqual(summary.recent_sent_count, 1)
            store.close()

    def test_cleanup_expired_removes_old_record(self):
        now = datetime(2026, 1, 10, tzinfo=UTC)
        old = now - timedelta(days=10)
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            store.record_publish(note(), PublishResult(PublishStatus.SENT), ttl_days=7, now=old)

            removed = store.cleanup_expired(now)

            self.assertEqual(removed, 1)
            self.assertFalse(store.is_active("n1", now))
            store.close()

    def test_failed_status_is_recorded_for_ttl_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            store.record_publish(note("n2"), PublishResult(PublishStatus.FAILED, error_message="bad"), ttl_days=7)

            summary = store.summary()

            self.assertTrue(store.is_active("n2"))
            self.assertEqual(summary.active_dedup_count, 1)
            self.assertEqual(summary.recent_failed_count, 1)
            row = store.conn.execute("SELECT status, sent_at, error_message FROM published_notes WHERE note_id = ?", ("n2",)).fetchone()
            self.assertEqual(row["status"], PublishStatus.FAILED.value)
            self.assertIsNone(row["sent_at"])
            self.assertEqual(row["error_message"], "bad")
            store.close()

    def test_failed_status_expires_and_becomes_eligible(self):
        now = datetime(2026, 1, 10, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp) / "db.sqlite")
            store.record_publish(
                note("n3"),
                PublishResult(PublishStatus.FAILED, error_message="bad"),
                ttl_days=7,
                now=now - timedelta(days=7),
            )

            self.assertTrue(store.is_active("n3", now - timedelta(seconds=1)))
            store.cleanup_expired(now + timedelta(seconds=1))
            self.assertFalse(store.is_active("n3", now + timedelta(seconds=1)))
            store.close()


if __name__ == "__main__":
    unittest.main()
