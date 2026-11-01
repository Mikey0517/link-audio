from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.providers.douyin import DouyinProvider


class AllowAllValidator:
    """Bypass DNS checks; MockTransport never touches the network."""

    async def validate(self, url: str, **_kwargs) -> str:
        return url


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        music_dir=tmp_path / "music",
        db_path=tmp_path / "data" / "music.db",
        _env_file=None,
        log_level="WARNING",
    )


def _aweme(aweme_id: str = "7345678901234567890") -> dict:
    return {
        "aweme_id": aweme_id,
        "desc": "真实网络层测试",
        "author": {"nickname": "网络作者"},
        "music": {
            "duration": 12000,
            "play_url": {"url_list": ["https://cdn.example/music.mp3"]},
        },
        "video": {
            "duration": 12000,
            "play_addr": {"url_list": ["https://cdn.example/video.mp4"]},
        },
    }


def _page_with_data(aweme_id: str = "7345678901234567890") -> bytes:
    payload = {
        "loaderData": {"video_(id)/page": {"videoInfoRes": {"item_list": [_aweme(aweme_id)]}}}
    }
    html = (
        "<html><body><script>window._ROUTER_DATA = "
        + json.dumps(payload, ensure_ascii=False)
        + ";</script></body></html>"
    )
    return html.encode("utf-8")


@pytest.mark.asyncio
async def test_parse_share_follows_short_link(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "v.douyin.com":
            return httpx.Response(
                302,
                headers={
                    "location": "https://www.iesdouyin.com/share/video/7345678901234567890/?region=CN"
                },
            )
        return httpx.Response(
            200,
            content=_page_with_data(),
            headers={"content-type": "text/html; charset=utf-8"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        douyin = DouyinProvider(settings, client, AllowAllValidator())  # type: ignore[arg-type]
        parsed = await douyin.parse_share("https://v.douyin.com/abc/")
    finally:
        await client.aclose()

    assert len(seen) == 2
    assert parsed.aweme_id == "7345678901234567890"
    assert parsed.author == "网络作者"
    assert parsed.music_urls == ("https://cdn.example/music.mp3",)
    assert parsed.video_urls == ("https://cdn.example/video.mp4",)
    assert parsed.media_type == "video"


@pytest.mark.asyncio
async def test_parse_share_falls_back_to_iteminfo(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    context_only = (
        b"<html><body><script>window._ROUTER_DATA = "
        b'{"loaderData":{"video_(id)/page":{"itemId":"7345678901234567890"}}}'
        b";</script></body></html>"
    )
    api_payload = json.dumps({"aweme_detail": _aweme()}).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "v.douyin.com":
            return httpx.Response(
                302,
                headers={"location": "https://www.iesdouyin.com/share/video/7345678901234567890/"},
            )
        if "/web/api/v2/aweme/iteminfo/" in request.url.path:
            return httpx.Response(
                200,
                content=api_payload,
                headers={"content-type": "application/json"},
            )
        return httpx.Response(
            200,
            content=context_only,
            headers={"content-type": "text/html; charset=utf-8"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        douyin = DouyinProvider(settings, client, AllowAllValidator())  # type: ignore[arg-type]
        parsed = await douyin.parse_share("https://v.douyin.com/abc/")
    finally:
        await client.aclose()

    assert parsed.aweme_id == "7345678901234567890"
    assert parsed.title == "真实网络层测试"
    assert parsed.media_type == "video"
