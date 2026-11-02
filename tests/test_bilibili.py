from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.providers.bilibili import BilibiliProvider, extract_bilibili_url


class AllowAllValidator:
    async def validate(self, url: str, **_kwargs) -> str:
        return url


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        music_dir=tmp_path / "music",
        db_path=tmp_path / "data" / "music.db",
        _env_file=None,
        log_level="WARNING",
    )


def _json_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


def test_extract_bilibili_short_link() -> None:
    text = (
        "【在百万豪装录音棚大声听 黄诗扶&妖扬《吹梦到西洲》【Hi-res】-哔哩哔哩】 "
        "https://b23.tv/86lPuit"
    )
    assert extract_bilibili_url(text) == "https://b23.tv/86lPuit"


@pytest.mark.asyncio
async def test_bilibili_provider_parses_dash_audio(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "b23.tv":
            return httpx.Response(
                302,
                headers={"location": "https://www.bilibili.com/video/BV1Dg4y1K7dN?p=1"},
            )
        if request.url.path == "/x/web-interface/view":
            return _json_response(
                {
                    "code": 0,
                    "message": "OK",
                    "data": {
                        "bvid": "BV1Dg4y1K7dN",
                        "aid": 827547598,
                        "cid": 1172379946,
                        "title": "吹梦到西洲",
                        "duration": 316,
                        "owner": {"name": "测试 UP 主"},
                        "pages": [
                            {
                                "cid": 1172379946,
                                "part": "正片",
                                "duration": 316,
                            }
                        ],
                    },
                }
            )
        if request.url.path == "/x/player/playurl":
            if request.url.params.get("fnval") == "16":
                return _json_response(
                    {
                        "code": 0,
                        "data": {
                            "timelength": 316000,
                            "dash": {
                                "duration": 316,
                                "audio": [
                                    {
                                        "id": 30280,
                                        "bandwidth": 250257,
                                        "baseUrl": "https://cdn.example:8082/audio.m4s",
                                        "backupUrl": ["https://cdn.example:4483/audio.m4s"],
                                    }
                                ],
                            },
                        },
                    }
                )
            return _json_response(
                {
                    "code": 0,
                    "data": {
                        "durl": [
                            {
                                "url": "https://cdn.example:4483/video.mp4",
                                "backup_url": "https://cdn.example:4483/video-2.mp4",
                                "size": 100,
                            }
                        ]
                    },
                }
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = BilibiliProvider(settings, client, AllowAllValidator())  # type: ignore[arg-type]
        parsed = await provider.parse("https://b23.tv/86lPuit")
    finally:
        await client.aclose()

    assert parsed.platform == "bilibili"
    assert parsed.external_id == "BV1Dg4y1K7dN"
    assert parsed.title == "吹梦到西洲"
    assert parsed.author == "测试 UP 主"
    assert parsed.duration_seconds == 316
    assert parsed.music_urls[0] == "https://cdn.example:8082/audio.m4s"
    assert parsed.video_urls == (
        "https://cdn.example:4483/video.mp4",
        "https://cdn.example:4483/video-2.mp4",
    )
    assert parsed.media_ports == (8081, 8082, 8083, 4483)
    assert parsed.referer == "https://www.bilibili.com/"
