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
async def test_bilibili_api_retries_transient_timeout(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    calls = {"view": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/web-interface/view":
            calls["view"] += 1
            if calls["view"] == 1:
                raise httpx.ReadTimeout("simulated timeout", request=request)
            return _json_response({"code": 0, "data": {"bvid": "BV1Dg4y1K7dN"}})
        raise AssertionError(f"unexpected URL: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = BilibiliProvider(settings, client, AllowAllValidator())  # type: ignore[arg-type]
        payload = await provider._api_json(
            "https://api.bilibili.com/x/web-interface/view?bvid=BV1Dg4y1K7dN",
            label="view",
        )
    finally:
        await client.aclose()

    assert payload["code"] == 0
    assert calls["view"] == 2


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
    assert parsed.video_urls == ()
    assert parsed.media_ports == (8081, 8082, 8083, 4483)
    assert parsed.referer == "https://www.bilibili.com/"


@pytest.mark.asyncio
async def test_bilibili_provider_falls_back_to_combined_stream(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "b23.tv":
            return httpx.Response(
                302,
                headers={"location": "https://www.bilibili.com/video/BV1Dg4y1K7dN"},
            )
        if request.url.path == "/x/web-interface/view":
            return _json_response(
                {
                    "code": 0,
                    "data": {
                        "bvid": "BV1Dg4y1K7dN",
                        "aid": 1,
                        "cid": 2,
                        "title": "无 DASH 音频",
                        "duration": 10,
                        "owner": {"name": "UP"},
                        "pages": [{"cid": 2, "part": "", "duration": 10}],
                    },
                }
            )
        if request.url.path == "/x/player/playurl":
            if request.url.params.get("fnval") == "16":
                return _json_response({"code": 0, "data": {"dash": {"audio": []}}})
            return _json_response(
                {
                    "code": 0,
                    "data": {
                        "durl": [
                            {
                                "url": "https://cdn.example:4483/video.mp4",
                                "backup_url": "https://cdn.example:4483/video-2.mp4",
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

    assert parsed.music_urls == ()
    assert parsed.video_urls == (
        "https://cdn.example:4483/video.mp4",
        "https://cdn.example:4483/video-2.mp4",
    )


@pytest.mark.asyncio
async def test_bilibili_provider_falls_back_to_mobile_page(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state = {
        "video": {
            "viewInfo": {
                "bvid": "BV1Dg4y1K7dN",
                "aid": 1,
                "cid": 2,
                "title": "页面兜底",
                "duration": 20,
                "owner": {"name": "页面 UP"},
                "pages": [{"cid": 2, "part": "", "duration": 20}],
            },
            "playUrlInfo": [
                {
                    "url": "https://cdn.example:4483/fallback.mp4",
                    "backup_url": "https://cdn.example:4483/fallback-2.mp4",
                }
            ],
        }
    }
    page_html = (
        "<html><body><script>window.__INITIAL_STATE__="
        + json.dumps(state, ensure_ascii=False)
        + ";</script></body></html>"
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "b23.tv":
            return httpx.Response(
                302,
                headers={"location": "https://www.bilibili.com/video/BV1Dg4y1K7dN"},
            )
        if request.url.path == "/x/web-interface/view":
            return httpx.Response(503, content=b"temporarily unavailable")
        if request.url.path == "/video/BV1Dg4y1K7dN":
            return httpx.Response(
                200,
                content=page_html,
                headers={"content-type": "text/html; charset=utf-8"},
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = BilibiliProvider(settings, client, AllowAllValidator())  # type: ignore[arg-type]
        parsed = await provider.parse("https://b23.tv/86lPuit")
    finally:
        await client.aclose()

    assert parsed.title == "页面兜底"
    assert parsed.author == "页面 UP"
    assert parsed.music_urls == ()
    assert parsed.video_urls == (
        "https://cdn.example:4483/fallback.mp4",
        "https://cdn.example:4483/fallback-2.mp4",
    )
