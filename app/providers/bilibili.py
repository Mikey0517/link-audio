"""Bilibili provider: b23.tv short links, video metadata and audio streams."""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

from ..config import Settings
from ..errors import (
    AppError,
    FetchError,
    InvalidURLError,
    NoAudioError,
    ParseError,
    UnsupportedDomainError,
)
from ..net import RemoteURLValidator, normalise_host, validate_http_url
from .base import BaseProvider, ParsedMedia

logger = logging.getLogger(__name__)

BILIBILI_BASE_DOMAINS = (
    "bilibili.com",
    "b23.tv",
    "bili2233.cn",
    "bili22.cn",
    "bili33.cn",
)
BILIBILI_MEDIA_PORTS = (8081, 8082, 8083, 4483)
BILIBILI_REFERER = "https://www.bilibili.com/"
BILIBILI_ORIGIN = "https://www.bilibili.com"
API_BASE = "https://api.bilibili.com"

_URL_IN_TEXT_RE = re.compile(
    r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]+",
    re.IGNORECASE,
)
_BARE_BILIBILI_RE = re.compile(
    r"(?<![A-Za-z0-9.\-])"
    r"((?:b23\.tv|bili2233\.cn|bili22\.cn|bili33\.cn|(?:www|m)\.bilibili\.com)"
    r"/[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]*)",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = " \t\r\n.,;:!?，。；：！？、）】》」』\"'"
_BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AID_RE = re.compile(r"(?:/video/av|^av)(\d{5,20})", re.IGNORECASE)


def is_bilibili_host(host: str | None) -> bool:
    host = normalise_host(host)
    return any(host == base or host.endswith("." + base) for base in BILIBILI_BASE_DOMAINS)


def validate_bilibili_url(url: str) -> str:
    validate_http_url(url)
    parsed = urlsplit(url)
    if not is_bilibili_host(parsed.hostname):
        raise UnsupportedDomainError("仅支持 bilibili.com、b23.tv、bili2233.cn 等哔哩哔哩相关域名")
    return url


def extract_bilibili_url(share_text: str) -> str:
    text = (share_text or "").strip()
    if not text:
        raise InvalidURLError("请先粘贴分享文案或链接")

    candidates: list[str] = []
    for match in _URL_IN_TEXT_RE.finditer(text):
        candidates.append(match.group(0))
    for match in _BARE_BILIBILI_RE.finditer(text):
        candidates.append("https://" + match.group(1))

    valid: list[str] = []
    last_error: AppError | None = None
    saw_http_url = False
    for raw in candidates:
        url = html_lib.unescape(raw).strip().rstrip(_TRAILING_PUNCTUATION)
        if not re.match(r"^https?://", url, re.IGNORECASE):
            continue
        saw_http_url = True
        try:
            host = normalise_host(urlsplit(url).hostname)
        except ValueError:
            continue
        if not is_bilibili_host(host):
            continue
        try:
            valid.append(validate_bilibili_url(url))
        except AppError as exc:
            last_error = exc

    # Prefer b23.tv / other short links over a normal bilibili.com URL.
    for url in valid:
        host = normalise_host(urlsplit(url).hostname)
        if host in {"b23.tv", "bili2233.cn", "bili22.cn", "bili33.cn"}:
            return url
    if valid:
        return valid[0]
    if last_error is not None:
        raise last_error
    if saw_http_url:
        raise UnsupportedDomainError(
            "未找到哔哩哔哩链接。仅支持 bilibili.com、b23.tv、bili2233.cn。"
        )
    raise InvalidURLError("未在文案中找到有效的哔哩哔哩链接")


def _video_identity(url: str) -> tuple[str | None, str | None]:
    parsed = urlsplit(url)
    path = parsed.path
    match = _BVID_RE.search(path)
    if match:
        return match.group(0), None
    match = _AID_RE.search(path)
    if match:
        return None, match.group(1)
    query = parse_qs(parsed.query)
    for key in ("bvid", "bvid_str"):
        value = (query.get(key) or [""])[0]
        if _BVID_RE.fullmatch(value or ""):
            return value, None
    value = (query.get("aid") or query.get("avid") or [""])[0]
    if value.isdigit():
        return None, value
    return None, None


def _page_number(url: str) -> int:
    parsed = urlsplit(url)
    value = (parse_qs(parsed.query).get("p") or ["1"])[0]
    try:
        return max(1, int(value))
    except ValueError:
        return 1


def _bvid_from_html(page_html: str) -> str | None:
    match = _BVID_RE.search(page_html)
    if match:
        return match.group(0)
    match = re.search(r'"bvid"\s*:\s*"?(BV[0-9A-Za-z]{10})', page_html)
    return match.group(1) if match else None


def _url_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


class BilibiliProvider(BaseProvider):
    name = "bilibili"
    display_name = "哔哩哔哩"

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
        validator: RemoteURLValidator,
    ) -> None:
        self._settings = settings
        self._http = http_client
        self._validator = validator

    def extract_url(self, share_text: str) -> str | None:
        try:
            return extract_bilibili_url(share_text)
        except AppError:
            return None

    def matches(self, url: str) -> bool:
        try:
            return is_bilibili_host(normalise_host(urlsplit(url).hostname))
        except ValueError:
            return False

    @property
    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._settings.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            "Referer": BILIBILI_REFERER,
            "Origin": BILIBILI_ORIGIN,
        }

    async def parse(self, share_url: str) -> ParsedMedia:
        logger.info("解析哔哩哔哩链接: %s", _safe_url(share_url))
        final_url = await self._resolve_share_url(share_url)
        bvid, aid = _video_identity(final_url)
        if not bvid and not aid:
            raise ParseError("未能识别哔哩哔哩视频 ID，页面结构可能已变化")

        page_number = _page_number(final_url)
        view = await self._api_json(self._view_url(bvid=bvid, aid=aid))
        data = view.get("data") or {}
        if not isinstance(data, dict) or not data:
            raise ParseError("哔哩哔哩接口未返回视频信息，作品可能不可见")
        bvid = str(data.get("bvid") or bvid or "")
        aid = str(data.get("aid") or aid or "")
        if not bvid:
            raise ParseError("哔哩哔哩接口未返回 BV 号")

        pages = data.get("pages") if isinstance(data.get("pages"), list) else []
        page = pages[page_number - 1] if 0 < page_number <= len(pages) else None
        if page is None and pages:
            page = pages[0]
        cid = str((page or {}).get("cid") or data.get("cid") or "")
        if not cid:
            raise ParseError("哔哩哔哩接口未返回 cid，无法获取音频")

        duration = _float_or_none((page or {}).get("duration")) or _float_or_none(
            data.get("duration")
        )
        title = str(data.get("title") or f"哔哩哔哩视频 {bvid}")
        if page is not None and page_number > 1:
            part = str(page.get("part") or "").strip()
            if part:
                title = f"{title} - {part}"
        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        author = str(owner.get("name") or "未知 UP 主")

        music_urls: list[str] = []
        video_urls: list[str] = []
        dash_data = await self._api_json(self._playurl_url(bvid=bvid, aid=aid, cid=cid, fnval=16))
        dash_payload = dash_data.get("data") or {}
        dash = dash_payload.get("dash") if isinstance(dash_payload, dict) else {}
        if isinstance(dash, dict):
            audio_items = dash.get("audio") if isinstance(dash.get("audio"), list) else []
            for item in sorted(
                (x for x in audio_items if isinstance(x, dict)),
                key=lambda x: int(x.get("bandwidth") or 0),
                reverse=True,
            ):
                for key in ("baseUrl", "base_url"):
                    for url in _url_list(item.get(key)):
                        _append_unique(music_urls, url)
                for key in ("backupUrl", "backup_url"):
                    for url in _url_list(item.get(key)):
                        _append_unique(music_urls, url)
            if duration is None:
                duration = _float_or_none(dash.get("duration"))

        # A combined MP4/FLV stream is useful when the DASH audio endpoint is
        # unavailable or the audio CDN rejects the request.
        try:
            combined = await self._api_json(self._playurl_url(bvid=bvid, aid=aid, cid=cid, fnval=1))
            combined_data = combined.get("data") or {}
            for item in combined_data.get("durl") or []:
                if not isinstance(item, dict):
                    continue
                for key in ("url", "backup_url"):
                    for url in _url_list(item.get(key)):
                        _append_unique(video_urls, url)
        except AppError as exc:
            logger.debug("哔哩哔哩组合流获取失败: %s", exc.message)

        if not music_urls and not video_urls:
            raise NoAudioError("未找到哔哩哔哩音频流，作品可能需要登录、付费或版权限制")

        logger.info(
            "哔哩哔哩解析成功: bvid=%s cid=%s title=%s audio=%d fallback=%d",
            bvid,
            cid,
            title[:80],
            len(music_urls),
            len(video_urls),
        )
        return ParsedMedia(
            platform=self.name,
            external_id=bvid,
            title=title[:300],
            author=author[:100],
            source_url=share_url,
            final_url=final_url,
            media_type="video",
            music_urls=tuple(music_urls),
            video_urls=tuple(video_urls),
            duration_seconds=duration,
            referer=BILIBILI_REFERER,
            origin=BILIBILI_ORIGIN,
            media_ports=BILIBILI_MEDIA_PORTS,
        )

    async def _resolve_share_url(self, url: str) -> str:
        current = url
        for _hop in range(8):
            bvid, aid = _video_identity(current)
            if bvid or aid:
                return current

            validate_bilibili_url(current)
            await self._validator.validate(current)
            try:
                async with self._http.stream(
                    "GET",
                    current,
                    headers={**self.headers, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
                    timeout=self._settings.page_timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("哔哩哔哩短链缺少 Location 跳转头")
                        current = urljoin(str(response.url), location)
                        logger.info("哔哩哔哩跳转 -> %s", _safe_url(current))
                        continue
                    if response.status_code != 200:
                        raise FetchError(
                            "哔哩哔哩页面请求失败",
                            detail=f"upstream status={response.status_code}",
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._settings.max_page_bytes:
                            raise FetchError("哔哩哔哩页面体积异常")
                    text = body.decode(response.encoding or "utf-8", errors="replace")
                    bvid = _bvid_from_html(text)
                    if bvid:
                        return f"https://www.bilibili.com/video/{bvid}"
                    raise ParseError("未在哔哩哔哩页面中找到视频 BV 号")
            except httpx.TimeoutException as exc:
                raise FetchError("请求哔哩哔哩页面超时") from exc
            except httpx.RequestError as exc:
                raise FetchError("无法连接哔哩哔哩", detail=str(exc)) from exc
        raise FetchError("哔哩哔哩跳转次数过多")

    def _view_url(self, *, bvid: str | None, aid: str | None) -> str:
        params = {"bvid": bvid} if bvid else {"aid": aid}
        return f"{API_BASE}/x/web-interface/view?{urlencode(params)}"

    def _playurl_url(self, *, bvid: str | None, aid: str | None, cid: str, fnval: int) -> str:
        params: dict[str, Any] = {
            "cid": cid,
            "fnval": fnval,
            "fourk": 1,
        }
        if bvid:
            params["bvid"] = bvid
        else:
            params["avid"] = aid
        return f"{API_BASE}/x/player/playurl?{urlencode(params)}"

    async def _api_json(self, url: str) -> dict[str, Any]:
        validate_bilibili_url(url)
        await self._validator.validate(url)
        try:
            async with self._http.stream(
                "GET",
                url,
                headers=self.headers,
                timeout=self._settings.page_timeout_seconds,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise FetchError(
                        "哔哩哔哩接口请求失败",
                        detail=f"upstream status={response.status_code}",
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._settings.max_page_bytes:
                        raise FetchError("哔哩哔哩接口响应过大")
        except httpx.TimeoutException as exc:
            raise FetchError("哔哩哔哩接口请求超时") from exc
        except httpx.RequestError as exc:
            raise FetchError("无法连接哔哩哔哩接口", detail=str(exc)) from exc

        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ParseError("哔哩哔哩接口返回了非 JSON 内容") from exc
        code = payload.get("code")
        if code != 0:
            message = str(payload.get("message") or "未知错误")
            raise FetchError(
                f"哔哩哔哩接口返回错误：{message}",
                detail=f"api code={code}",
            )
        return payload


def _append_unique(urls: list[str], value: str) -> None:
    if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in urls:
        urls.append(value)


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return round(number, 3)


def _safe_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
