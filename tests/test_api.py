from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def app_client(tmp_path: Path):
    settings = Settings(
        music_dir=tmp_path / "music",
        db_path=tmp_path / "data" / "music.db",
        _env_file=None,
        log_level="WARNING",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield app, client, settings


def _seed(
    app, settings: Settings, data: bytes = b"ID3" + bytes(range(256)) * 10
) -> tuple[int, Path]:
    record_id = app.state.db.create_processing(
        aweme_id="7345678901234567890",
        title="Range 测试",
        author="测试作者",
        source_url="https://v.douyin.com/test/",
        media_type="video",
    )
    path = settings.music_dir / "range-test.mp3"
    path.write_bytes(data)
    app.state.db.mark_completed(
        record_id,
        file_path=path.name,
        file_size=len(data),
        duration_seconds=12.5,
        source_kind="video_ffmpeg",
    )
    return record_id, path


def test_pages_are_split(app_client) -> None:
    _app, client, _settings = app_client
    extract_page = client.get("/")
    assert extract_page.status_code == 200
    assert "解析音频" in extract_page.text
    assert "/static/extract.js" in extract_page.text

    library_page = client.get("/library")
    assert library_page.status_code == 200
    assert "音乐库" in library_page.text
    assert "/static/library.js" in library_page.text


def test_health_and_empty_list(app_client) -> None:
    _app, client, _settings = app_client
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert body["music_dir_writable"] is True
    assert body["providers"] == ["douyin", "bilibili"]

    response = client.get("/api/audios")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_full_audio_get_and_head(app_client) -> None:
    app, client, settings = app_client
    record_id, _path = _seed(app, settings)

    response = client.get(f"/audio/{record_id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["accept-ranges"] == "bytes"
    assert int(response.headers["content-length"]) == len(response.content)

    head = client.head(f"/audio/{record_id}")
    assert head.status_code == 200
    assert head.headers["content-type"] == "audio/mpeg"
    assert head.headers["accept-ranges"] == "bytes"
    assert int(head.headers["content-length"]) == len(response.content)
    assert head.content == b""


@pytest.mark.parametrize(
    ("range_header", "expected_status", "expected_start", "expected_end"),
    [
        ("bytes=0-9", 206, 0, 9),
        ("bytes=10-19", 206, 10, 19),
        ("bytes=100-", 206, 100, 2562),
        ("bytes=-10", 206, 2553, 2562),
        ("bytes=0-999999", 206, 0, 2562),
    ],
)
def test_range_requests(
    app_client,
    range_header: str,
    expected_status: int,
    expected_start: int,
    expected_end: int,
) -> None:
    app, client, settings = app_client
    record_id, _path = _seed(app, settings)

    response = client.get(f"/audio/{record_id}", headers={"Range": range_header})
    assert response.status_code == expected_status
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == f"bytes {expected_start}-{expected_end}/2563"
    assert int(response.headers["content-length"]) == expected_end - expected_start + 1
    assert response.content == (b"ID3" + bytes(range(256)) * 10)[expected_start : expected_end + 1]

    head = client.head(f"/audio/{record_id}", headers={"Range": range_header})
    assert head.status_code == expected_status
    assert head.content == b""
    assert head.headers["content-range"] == response.headers["content-range"]


def test_range_not_satisfiable(app_client) -> None:
    app, client, settings = app_client
    record_id, _path = _seed(app, settings)
    response = client.get(f"/audio/{record_id}", headers={"Range": "bytes=99999-"})
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */2563"
    assert response.json()["detail"]["code"] == "RANGE_NOT_SATISFIABLE"

    malformed = client.get(f"/audio/{record_id}", headers={"Range": "bytes=abc-def"})
    assert malformed.status_code == 416


def test_list_metadata_shape(app_client) -> None:
    app, client, settings = app_client
    record_id, _path = _seed(app, settings)
    response = client.get("/api/audios")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["id"] == record_id
    assert item["platform"] == "douyin"
    assert item["audio_url"] == f"/audio/{record_id}"
    assert item["title"] == "Range 测试"
    assert item["author"] == "测试作者"
    assert item["duration_seconds"] == 12.5
    assert item["file_size"] == 2563
    assert "file_path" not in item


def test_delete_removes_db_and_file(app_client) -> None:
    app, client, settings = app_client
    record_id, path = _seed(app, settings)
    assert path.exists()

    response = client.delete(f"/api/audios/{record_id}")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert not path.exists()
    assert app.state.db.get(record_id) is None
    assert client.get(f"/audio/{record_id}").status_code == 404
    assert client.get("/api/audios").json()["total"] == 0


def test_missing_file_returns_404(app_client) -> None:
    app, client, settings = app_client
    record_id, path = _seed(app, settings)
    path.unlink()
    response = client.get(f"/audio/{record_id}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "FILE_MISSING"


def test_validation_error_is_json(app_client) -> None:
    _app, client, _settings = app_client
    response = client.post("/api/extract", json={"share_text": "   "})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_progress_endpoint_unknown_job(app_client) -> None:
    _app, client, _settings = app_client
    response = client.get("/api/extract/progress/not-a-real-job")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PROGRESS_NOT_FOUND"


def test_extract_reports_progress(app_client) -> None:
    app, client, _settings = app_client

    class FakeService:
        async def extract(self, share_text, progress=None):
            if progress:
                progress(30, "正在下载音频")
                progress(80, "正在转码为 MP3")
            return (
                {
                    "id": 1,
                    "platform": "bilibili",
                    "aweme_id": "BV1Dg4y1K7dN",
                    "title": "进度测试",
                    "author": "UP",
                    "source_url": "https://b23.tv/test",
                    "file_path": "test.mp3",
                    "file_size": 100,
                    "duration_seconds": 10,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "status": "completed",
                    "media_type": "video",
                    "source_kind": "music_transcoded",
                },
                False,
            )

    app.state.service = FakeService()
    response = client.post(
        "/api/extract",
        json={"share_text": "https://b23.tv/test", "job_id": "test-job-123"},
    )
    assert response.status_code == 200
    progress = client.get("/api/extract/progress/test-job-123")
    assert progress.status_code == 200
    body = progress.json()
    assert body["status"] == "completed"
    assert body["percent"] == 100
    assert body["stage"] == "完成"
