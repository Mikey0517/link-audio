from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.downloader import HTTPDownloader
from app.errors import DownloadError, MediaTooLargeError


class AllowAllValidator:
    async def validate(self, url: str, **_kwargs) -> str:
        return url


def _settings(tmp_path: Path, max_mb: int = 1) -> Settings:
    settings = Settings(
        music_dir=tmp_path / "music",
        db_path=tmp_path / "data" / "music.db",
        max_download_mb=max_mb,
        _env_file=None,
        log_level="WARNING",
    )
    settings.prepare_directories()
    return settings


async def _download(tmp_path: Path, handler, *, max_bytes: int | None = None):
    settings = _settings(tmp_path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    downloader = HTTPDownloader(settings, client, AllowAllValidator())  # type: ignore[arg-type]
    destination = settings.temp_dir / "test.part"
    try:
        return await downloader.download(
            "https://cdn.example/media.mp3",
            destination,
            referer="https://www.douyin.com/",
            origin="https://www.douyin.com",
            max_bytes=max_bytes,
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_download_streams_file(tmp_path: Path) -> None:
    payload = b"ID3" + b"\x11" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["referer"] == "https://www.douyin.com/"
        assert request.headers["origin"] == "https://www.douyin.com"
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "audio/mpeg", "content-length": str(len(payload))},
        )

    result = await _download(tmp_path, handler)
    assert result.path.is_file()
    assert result.path.read_bytes() == payload
    assert result.size == len(payload)
    assert result.content_type == "audio/mpeg"


@pytest.mark.asyncio
async def test_download_follows_redirect(tmp_path: Path) -> None:
    payload = b"ID3" + b"\x22" * 100

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/media.mp3":
            return httpx.Response(302, headers={"location": "/real.mp3"})
        return httpx.Response(200, content=payload, headers={"content-type": "audio/mpeg"})

    result = await _download(tmp_path, handler)
    assert result.path.read_bytes() == payload


@pytest.mark.asyncio
async def test_download_rejects_html(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<!doctype html><html><body>blocked</body></html>",
            headers={"content-type": "text/html"},
        )

    with pytest.raises(DownloadError):
        await _download(tmp_path, handler)


@pytest.mark.asyncio
async def test_download_rejects_declared_oversize(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 100,
            headers={"content-type": "audio/mpeg", "content-length": "999999"},
        )

    with pytest.raises(MediaTooLargeError):
        await _download(tmp_path, handler, max_bytes=1000)


@pytest.mark.asyncio
async def test_download_rejects_stream_oversize(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 5000,
            headers={"content-type": "audio/mpeg"},
        )

    with pytest.raises(MediaTooLargeError):
        await _download(tmp_path, handler, max_bytes=1000)
