from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rednote2tg.models import Note, PublishResult, PublishStatus

PUBLISHED_STATUSES = (PublishStatus.SENT.value, PublishStatus.SENT_DEGRADED.value)
PUBLISHED_STATUS_SET = {PublishStatus.SENT, PublishStatus.SENT_DEGRADED}


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class StatusSummary:
    active_dedup_count: int
    recent_sent_count: int
    recent_failed_count: int


class NoteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS published_notes (
                note_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_key TEXT NOT NULL,
                note_url TEXT NOT NULL,
                title TEXT,
                first_seen_at TEXT NOT NULL,
                sent_at TEXT,
                expire_at TEXT NOT NULL,
                status TEXT NOT NULL,
                telegram_message_ids TEXT NOT NULL DEFAULT '[]',
                error_message TEXT
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_published_notes_expire_at ON published_notes(expire_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_published_notes_status ON published_notes(status)")
        self.conn.commit()

    def cleanup_expired(self, now: datetime | None = None) -> int:
        now = now or utc_now()
        cur = self.conn.execute("DELETE FROM published_notes WHERE expire_at < ?", (now.isoformat(),))
        self.conn.commit()
        return int(cur.rowcount)

    def active_note_ids(self, now: datetime | None = None) -> set[str]:
        now = now or utc_now()
        rows = self.conn.execute(
            "SELECT note_id FROM published_notes WHERE expire_at >= ? AND status IN (?, ?)",
            (now.isoformat(), *PUBLISHED_STATUSES),
        ).fetchall()
        return {str(row["note_id"]) for row in rows}

    def is_active(self, note_id: str, now: datetime | None = None) -> bool:
        return note_id in self.active_note_ids(now)

    def record_publish(
        self,
        note: Note,
        result: PublishResult,
        ttl_days: int,
        now: datetime | None = None,
    ) -> None:
        if result.status not in PUBLISHED_STATUS_SET:
            return

        now = now or utc_now()
        expire_at = now + timedelta(days=ttl_days)
        sent_at = now.isoformat()
        self.conn.execute(
            """
            INSERT INTO published_notes (
                note_id, source_type, source_key, note_url, title,
                first_seen_at, sent_at, expire_at, status,
                telegram_message_ids, error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                source_type = excluded.source_type,
                source_key = excluded.source_key,
                note_url = excluded.note_url,
                title = excluded.title,
                sent_at = excluded.sent_at,
                expire_at = excluded.expire_at,
                status = excluded.status,
                telegram_message_ids = excluded.telegram_message_ids,
                error_message = excluded.error_message
            """,
            (
                note.note_id,
                note.source.source_type,
                note.source.source_key,
                note.url,
                note.title,
                now.isoformat(),
                sent_at,
                expire_at.isoformat(),
                result.status.value,
                json.dumps(list(result.telegram_message_ids), ensure_ascii=False),
                result.error_message,
            ),
        )
        self.conn.commit()

    def summary(self, now: datetime | None = None) -> StatusSummary:
        now = now or utc_now()
        active = self.conn.execute(
            "SELECT COUNT(*) AS c FROM published_notes WHERE expire_at >= ? AND status IN (?, ?)",
            (now.isoformat(), *PUBLISHED_STATUSES),
        ).fetchone()["c"]
        sent = self.conn.execute(
            "SELECT COUNT(*) AS c FROM published_notes WHERE status IN (?, ?)",
            PUBLISHED_STATUSES,
        ).fetchone()["c"]
        failed = self.conn.execute(
            "SELECT COUNT(*) AS c FROM published_notes WHERE status = ?",
            (PublishStatus.FAILED.value,),
        ).fetchone()["c"]
        return StatusSummary(active, sent, failed)
