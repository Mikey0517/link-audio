from __future__ import annotations

from pathlib import Path

import pytest

from app.audio_service import ExtractionService
from app.config import Settings
from app.errors import DownloadError, FFmpegMissingError
from app.providers.base import ParsedMedia


class FakeDownloader:
    def __init__(self, fail_music: bool = False) -> None:
        self.fail_music = fail_music
        self.requested: list[str] = []

    async def download(self, url, destination: Path, *, referer=None, origin=None, max_bytes=None):
        self.requested.append(url)
        if self.fail_music and "music" in url:
            raise DownloadError("模拟音乐下载失败")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"ID3" + b"\x00" * 2048)
        return destination


class FakeFFmpeg:
    def __init__(
        self,
        *,
        available: bool = True,
        music_duration: float | None = 15.0,
        video_duration: float | None = 15.0,
    ) -> None:
        self.ffmpeg_available = available
        self.music_duration = music_duration
        self.video_duration = video_duration
        self.extract_calls = 0
        self.transcode_calls = 0
        self.probe_calls = 0

    async def probe_duration(self, path: Path) -> float | None:
        self.probe_calls += 1
        name = path.name
        if "-music-" in name:
            return self.music_duration
        if "-video-" in name:
            return self.video_duration
        return self.video_duration

    def _write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ID3" + b"\x00" * 4096)

    async def extract_audio_from_url(self, url, destination: Path, **kwargs) -> None:
        self.extract_calls += 1
        self._write(destination)

    async def transcode_to_mp3(self, source: Path, destination: Path, **kwargs) -> None:
        self.transcode_calls += 1
        self._write(destination)


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        music_dir=tmp_path / "music",
        db_path=tmp_path / "data" / "music.db",
        _env_file=None,
        log_level="WARNING",
    )
    settings.prepare_directories()
    return settings


def _parsed(
    *,
    media_type: str = "video",
    music_urls: tuple[str, ...] = ("https://cdn.example/music.mp3",),
    video_urls: tuple[str, ...] = ("https://cdn.example/video.mp4",),
    duration: float | None = 15.0,
) -> ParsedMedia:
    return ParsedMedia(
        platform="douyin",
        external_id="7345678901234567890",
        title="测试",
        author="作者",
        source_url="https://v.douyin.com/abc/",
        final_url="https://www.douyin.com/video/7345678901234567890",
        media_type=media_type,
        music_urls=music_urls,
        video_urls=video_urls,
        duration_seconds=duration,
    )


@pytest.mark.asyncio
async def test_image_uses_direct_music_without_ffmpeg(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    downloader = FakeDownloader()
    ffmpeg = FakeFFmpeg()
    service = ExtractionService(settings, None, None, downloader, ffmpeg)  # type: ignore[arg-type]

    path, kind = await service._produce_audio(
        _parsed(media_type="image", video_urls=()),
        1,
    )
    assert path.is_file()
    assert path.name.endswith(".mp3")
    assert kind == "music"
    assert ffmpeg.extract_calls == 0
    assert ffmpeg.transcode_calls == 0
    assert ffmpeg.probe_calls == 0
    assert downloader.requested == ["https://cdn.example/music.mp3"]


@pytest.mark.asyncio
async def test_video_rejects_short_background_music_and_uses_ffmpeg(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    downloader = FakeDownloader()
    ffmpeg = FakeFFmpeg(music_duration=4.0, video_duration=15.0)
    service = ExtractionService(settings, None, None, downloader, ffmpeg)  # type: ignore[arg-type]

    path, kind = await service._produce_audio(_parsed(), 2)
    assert path.is_file()
    assert kind == "video_ffmpeg"
    assert ffmpeg.extract_calls == 1
    assert downloader.requested == ["https://cdn.example/music.mp3"]


@pytest.mark.asyncio
async def test_video_accepts_complete_music_directly(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    downloader = FakeDownloader()
    ffmpeg = FakeFFmpeg(music_duration=15.2, video_duration=15.0)
    service = ExtractionService(settings, None, None, downloader, ffmpeg)  # type: ignore[arg-type]

    path, kind = await service._produce_audio(_parsed(), 3)
    assert path.is_file()
    assert kind == "music"
    assert ffmpeg.extract_calls == 0


@pytest.mark.asyncio
async def test_video_falls_back_when_music_download_fails(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    downloader = FakeDownloader(fail_music=True)
    ffmpeg = FakeFFmpeg()
    service = ExtractionService(settings, None, None, downloader, ffmpeg)  # type: ignore[arg-type]

    path, kind = await service._produce_audio(_parsed(), 4)
    assert path.is_file()
    assert kind == "video_ffmpeg"
    assert ffmpeg.extract_calls == 1


@pytest.mark.asyncio
async def test_image_falls_back_to_video_when_music_fails(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    downloader = FakeDownloader(fail_music=True)
    ffmpeg = FakeFFmpeg()
    service = ExtractionService(settings, None, None, downloader, ffmpeg)  # type: ignore[arg-type]

    path, kind = await service._produce_audio(
        _parsed(media_type="image", duration=8.0),
        5,
    )
    assert path.is_file()
    assert kind == "video_ffmpeg"
    assert ffmpeg.extract_calls == 1


@pytest.mark.asyncio
async def test_video_without_ffmpeg_and_no_music_raises(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    downloader = FakeDownloader()
    ffmpeg = FakeFFmpeg(available=False)
    service = ExtractionService(settings, None, None, downloader, ffmpeg)  # type: ignore[arg-type]

    with pytest.raises(FFmpegMissingError):
        await service._produce_audio(_parsed(music_urls=()), 6)
