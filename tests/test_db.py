from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import Database

OLD_SCHEMA = """
CREATE TABLE audios (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
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


def test_existing_douyin_database_gets_platform_column(tmp_path: Path) -> None:
    path = tmp_path / "music.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        """
        INSERT INTO audios
            (aweme_id, title, author, source_url, created_at, status,
             media_type, source_kind, updated_at)
        VALUES ('7000000000000000001', '旧数据', '作者',
                'https://v.douyin.com/old/', '2026-01-01T00:00:00+00:00',
                'completed', 'video', 'music', '2026-01-01T00:00:00+00:00')
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()
    record = db.get(1)
    assert record is not None
    assert record["platform"] == "douyin"
    assert db.find_completed_by_media("douyin", "7000000000000000001") is not None


def test_new_records_are_platform_scoped(tmp_path: Path) -> None:
    db = Database(tmp_path / "music.db")
    db.initialize()

    first = db.create_processing(
        platform="douyin",
        aweme_id="7000000000000000001",
        title="抖音",
        author="A",
        source_url="https://v.douyin.com/a/",
        media_type="video",
    )
    second = db.create_processing(
        platform="fake",
        aweme_id="7000000000000000001",
        title="其他平台",
        author="B",
        source_url="https://fake.example/a",
        media_type="image",
    )
    db.mark_completed(
        first,
        file_path="one.mp3",
        file_size=10,
        duration_seconds=1.0,
        source_kind="music",
    )
    db.mark_completed(
        second,
        file_path="two.mp3",
        file_size=20,
        duration_seconds=2.0,
        source_kind="music",
    )

    assert db.find_completed_by_media("douyin", "7000000000000000001")["id"] == first
    assert db.find_completed_by_media("fake", "7000000000000000001")["id"] == second
    assert db.find_completed_by_media("other", "7000000000000000001") is None
    assert {item["platform"] for item in db.list_completed()} == {"douyin", "fake"}
