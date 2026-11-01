from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from app.errors import InvalidURLError, NoAudioError, UnsupportedDomainError
from app.providers.douyin import (
    _build_parsed,
    extract_aweme_id_from_url,
    extract_douyin_url,
    parse_page_html,
)


def _router_html(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False)
    return f"""<!doctype html>
<html><head><title>抖音</title></head>
<body>
<script>window._ROUTER_DATA = {raw};</script>
</body></html>"""


def _video_payload(aweme_id: str = "7345678901234567890", related: bool = False) -> dict:
    items = [
        {
            "aweme_id": "7000000000000000001",
            "desc": "相关推荐，不应被选中",
            "author": {"nickname": "推荐作者"},
            "music": {"play_url": {"url_list": ["https://cdn.example/related.mp3"]}},
            "video": {"play_addr": {"url_list": ["https://cdn.example/related.mp4"]}},
        },
        {
            "aweme_id": aweme_id,
            "desc": "测试视频 #音乐",
            "author": {"nickname": "测试作者"},
            "aweme_type": 0,
            "music": {
                "duration": 15000,
                "play_url": {
                    "url_list": ["https://sf3-cdn-tos.douyinstatic.com/obj/ies-music/test.mp3"]
                },
            },
            "video": {
                "duration": 15200,
                "play_addr": {
                    "url_list": ["https://www.douyin.com/aweme/v1/play/?video_id=test&ratio=1080p"]
                },
            },
        },
    ]
    if not related:
        items = items[1:]
    return {"loaderData": {"video_(id)/page": {"videoInfoRes": {"item_list": items}}}}


def test_extract_share_text_url() -> None:
    text = (
        "8.25 复制打开抖音，看看【测试作者的作品】不错 http://example.com/x "
        "https://www.douyin.com/video/7000000000000000001 "
        "https://v.douyin.com/iAbCdEf/ 3@8.com :9pm"
    )
    assert extract_douyin_url(text) == "https://v.douyin.com/iAbCdEf/"


def test_extract_url_without_scheme() -> None:
    assert (
        extract_douyin_url("复制打开抖音 v.douyin.com/AbCdEf/ 看看")
        == "https://v.douyin.com/AbCdEf/"
    )


def test_extract_rejects_non_douyin() -> None:
    with pytest.raises(UnsupportedDomainError) as excinfo:
        extract_douyin_url("https://example.com/video/123")
    assert "抖音" in str(excinfo.value)


def test_extract_no_url() -> None:
    with pytest.raises(InvalidURLError):
        extract_douyin_url("这里没有链接")


def test_parse_target_video() -> None:
    html = _router_html(_video_payload(related=True))
    aweme = parse_page_html(html, "7345678901234567890")
    assert aweme is not None
    assert aweme["aweme_id"] == "7345678901234567890"

    parsed = _build_parsed(
        aweme,
        source_url="https://v.douyin.com/abc/",
        final_url="https://www.iesdouyin.com/share/video/7345678901234567890/",
        target_id="7345678901234567890",
    )
    assert parsed.media_type == "video"
    assert parsed.author == "测试作者"
    assert parsed.duration_seconds == 15.2
    assert parsed.music_urls[0].endswith("test.mp3")
    assert "aweme/v1/play" in parsed.video_urls[0]


def test_parse_image_and_render_data() -> None:
    payload = {
        "loaderData": {
            "note_(id)/page": {
                "videoInfoRes": {
                    "item_list": [
                        {
                            "aweme_id": "7345678901234567891",
                            "desc": "图文作品",
                            "aweme_type": 68,
                            "author": {"nickname": "图文作者"},
                            "images": [{"url_list": ["https://cdn.example/1.jpg"]}],
                            "music": {
                                "duration": 8000,
                                "play_url": {"url_list": ["https://cdn.example/image-music.mp3"]},
                            },
                        }
                    ]
                }
            }
        }
    }
    encoded = quote(json.dumps(payload, ensure_ascii=False))
    html = (
        '<html><body><script id="RENDER_DATA" type="application/json">'
        + encoded
        + "</script></body></html>"
    )
    aweme = parse_page_html(html, "7345678901234567891")
    assert aweme is not None
    parsed = _build_parsed(
        aweme,
        source_url="https://v.douyin.com/img/",
        final_url="https://www.iesdouyin.com/share/note/7345678901234567891/",
        target_id="7345678901234567891",
    )
    assert parsed.media_type == "image"
    assert parsed.duration_seconds == 8.0
    assert parsed.music_urls == ("https://cdn.example/image-music.mp3",)
    assert parsed.video_urls == ()


def test_build_without_audio_raises() -> None:
    with pytest.raises(NoAudioError):
        _build_parsed(
            {"aweme_id": "7345678901234567890", "desc": "无音频"},
            source_url="https://v.douyin.com/x/",
            final_url="https://www.douyin.com/video/7345678901234567890",
            target_id="7345678901234567890",
        )


def test_extract_aweme_id_from_urls() -> None:
    assert (
        extract_aweme_id_from_url("https://www.douyin.com/video/7345678901234567890")
        == "7345678901234567890"
    )
    assert (
        extract_aweme_id_from_url(
            "https://www.iesdouyin.com/share/note/7345678901234567891/?region=CN"
        )
        == "7345678901234567891"
    )
    assert (
        extract_aweme_id_from_url("https://www.douyin.com/?modal_id=7345678901234567892")
        == "7345678901234567892"
    )
