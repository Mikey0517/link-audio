"""Small SQLite repository.

Each operation opens a short-lived connection. That keeps the code
thread-friendly (we call these methods through ``asyncio.to_thread``) and avoids
sharing a connection between request tasks.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS audios (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    platform         TEXT    NOT NULL DEFAULT 'douyin',
    aweme_id         TEXT    NOT NULL,
    title            TEXT    NOT NULL,
    author           TEXT    NOT NULL,
    source_url       TEXT    NOT NULL,
    file_path        TEXT,
    file_size        INTEGER,
    duration_seconds REAL,
    created_at       TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'processing',
    error_message    TEXT,
    media_type       TEXT    NOT NULL DEFAULT 'video',
    source_kind      TEXT    NOT NULL DEFAULT '',
    updated_at       TEXT    NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_audios_aweme_id ON audios(aweme_id);
CREATE INDEX IF NOT EXISTS idx_audios_platform_aweme
    ON audios(platform, aweme_id);
CREATE INDEX IF NOT EXISTS idx_audios_status_created
    ON audios(status, created_at DESC);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(audios)").fetchall()
            }
            if "platform" not in columns:
                # Existing databases from the Douyin-only version.
                conn.execute(
                    "ALTER TABLE audios ADD COLUMN platform TEXT NOT NULL DEFAULT 'douyin'"
                )
            conn.executescript(INDEXES)

    def ping(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def recover_interrupted(self) -> int:
        """Mark rows left in ``processing`` by a previous process as failed."""
        now = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE audios
                   SET status = 'failed',
                       error_message = '服务重启，提取任务被中断，请重新提取',
                       updated_at = ?
                 WHERE status = 'processing'
                """,
                (now,),
            )
            return cur.rowcount or 0

    def create_processing(
        self,
        *,
        platform: str = "douyin",
        aweme_id: str,
        title: str,
        author: str,
        source_url: str,
        media_type: str,
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO audios
                    (platform, aweme_id, title, author, source_url, created_at,
                     status, media_type, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'processing', ?, ?)
                """,
                (platform, aweme_id, title, author, source_url, now, media_type, now),
            )
            return int(cur.lastrowid)

    def mark_completed(
        self,
        audio_id: int,
        *,
        file_path: str,
        file_size: int,
        duration_seconds: float | None,
        source_kind: str,
    ) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE audios
                   SET file_path = ?, file_size = ?, duration_seconds = ?,
                       source_kind = ?, status = 'completed',
                       error_message = NULL, updated_at = ?
                 WHERE id = ?
                """,
                (
                    file_path,
                    file_size,
                    duration_seconds,
                    source_kind,
                    utc_now(),
                    audio_id,
                ),
            )
            if cur.rowcount == 0:
                raise sqlite3.DatabaseError(f"audio id {audio_id} 不存在")

    def mark_failed(self, audio_id: int, error_message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE audios
                   SET status = 'failed', error_message = ?, updated_at = ?
                 WHERE id = ?
                """,
                (error_message[:2000], utc_now(), audio_id),
            )

    def get(self, audio_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM audios WHERE id = ?", (audio_id,)).fetchone()
        return dict(row) if row else None

    def list_completed(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM audios
                 WHERE status = 'completed'
                 ORDER BY created_at DESC, id DESC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_completed_by_media(
        self,
        platform: str,
        aweme_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM audios
                 WHERE platform = ? AND aweme_id = ? AND status = 'completed'
                 ORDER BY id DESC LIMIT 1
                """,
                (platform, aweme_id),
            ).fetchone()
        return dict(row) if row else None

    def find_completed_by_aweme(self, aweme_id: str) -> dict[str, Any] | None:
        """Backward-compatible helper for the original Douyin-only schema."""

        return self.find_completed_by_media("douyin", aweme_id)

    def delete(self, audio_id: int) -> dict[str, Any] | None:
        record = self.get(audio_id)
        if record is None:
            return None
        with self._connect() as conn:
            conn.execute("DELETE FROM audios WHERE id = ?", (audio_id,))
        return record
