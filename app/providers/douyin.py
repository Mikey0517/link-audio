"""Douyin share-page fetching and metadata extraction.

The implementation intentionally does not depend on a paid/private parsing API:

1. extract a Douyin URL from the pasted share text;
2. follow the short-link redirects ourselves (validating every hop);
3. load the mobile share page with a mobile User-Agent;
4. parse ``window._ROUTER_DATA`` / ``RENDER_DATA`` / initial-state JSON;
5. fall back to the public item-info endpoint if the page contains no data.

Douyin changes its frontend regularly. The JSON walker below is deliberately
schema-tolerant and looks for any aweme object containing music/video/images.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit

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
from ..net import RemoteURLValidator, validate_http_url
from ..net import normalise_host as _normalise_host
from .base import BaseProvider, ParsedMedia

logger = logging.getLogger(__name__)

DOUYIN_BASE_DOMAINS = ("douyin.com", "iesdouyin.com")

_URL_IN_TEXT_RE = re.compile(
    r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]+",
    re.IGNORECASE,
)
_BARE_DOUYIN_RE = re.compile(
    r"(?<![A-Za-z0-9.\-])"
    r"((?:v|www|m)\.(?:douyin|iesdouyin)\.com"
    r"/[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]*)",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = " \t\r\n.,;:!?，。；：！？、）】》」』\"'"
_AWEME_ID_RE = re.compile(r"^\d{5,30}$")


def is_douyin_host(host: str | None) -> bool:
    host = _normalise_host(host)
    return any(host == base or host.endswith("." + base) for base in DOUYIN_BASE_DOMAINS)


def validate_douyin_url(url: str) -> str:
    """Only Douyin-family HTTP(S) hosts are allowed through this provider."""

    validate_http_url(url)
    parsed = urlsplit(url)
    host = _normalise_host(parsed.hostname)
    if not is_douyin_host(host):
        raise UnsupportedDomainError("仅支持 douyin.com、v.douyin.com、iesdouyin.com 域名下的链接")
    return url


def extract_douyin_url(share_text: str) -> str:
    """Extract the first usable Douyin URL from a share message."""

    text = (share_text or "").strip()
    if not text:
        raise InvalidURLError("请先粘贴抖音分享文案或链接")

    candidates: list[str] = []
    for match in _URL_IN_TEXT_RE.finditer(text):
        candidates.append(match.group(0))

    # Permissive fallback for a share text where the scheme was stripped.
    for match in _BARE_DOUYIN_RE.finditer(text):
        candidates.append("https://" + match.group(1))

    saw_http_url = False
    valid: list[str] = []
    last_error: AppError | None = None
    for raw in candidates:
        url = html_lib.unescape(raw).strip().rstrip(_TRAILING_PUNCTUATION)
        if not url:
            continue
        if not re.match(r"^https?://", url, re.IGNORECASE):
            continue
        saw_http_url = True
        host = ""
        try:
            host = _normalise_host(urlsplit(url).hostname)
        except ValueError:
            continue
        if not is_douyin_host(host):
            continue
        try:
            valid.append(validate_douyin_url(url))
        except AppError as exc:
            last_error = exc

    # Share text normally contains a v.douyin.com short link. Prefer it over
    # any other douyin.com URL that happens to appear earlier in the message.
    for url in valid:
        if _normalise_host(urlsplit(url).hostname) == "v.douyin.com":
            return url

    if valid:
        return valid[0]
    if last_error is not None:
        raise last_error
    if saw_http_url:
        raise UnsupportedDomainError(
            "未找到抖音链接。仅支持 douyin.com、v.douyin.com、iesdouyin.com。"
        )
    raise InvalidURLError("未在文案中找到有效的抖音链接")


def extract_aweme_id_from_url(url: str) -> str | None:
    """Try to find the target aweme id in a final/redirected URL."""

    try:
        parsed = urlsplit(url)
    except ValueError:
        return None

    path = unquote(parsed.path)
    patterns = (
        r"/(?:share/)?(?:video|note|slides)/(\d{5,30})(?:/|$)",
        r"/aweme/detail/(\d{5,30})(?:/|$)",
        r"(?:video|note|slides)/(\d{5,30})(?:/|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, path, re.IGNORECASE)
        if match:
            return match.group(1)

    for key in ("modal_id", "aweme_id", "item_id", "item_ids"):
        values = parsed.query
        for part in values.split("&"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            if k == key and _AWEME_ID_RE.match(unquote(v)):
                return unquote(v)
    return None


def _duration_seconds(value: Any) -> float | None:
    """Douyin reports durations in milliseconds."""

    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    seconds = number / 1000.0
    if seconds <= 0 or seconds > 24 * 60 * 60:
        return None
    return round(seconds, 3)


def _normalise_media_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = html_lib.unescape(value).strip()
    value = value.replace("\\/", "/").replace("\\u0026", "&").replace("\\u003d", "=")
    if value.startswith("//"):
        value = "https:" + value
    if not re.match(r"^https?://", value, re.IGNORECASE):
        return None
    if len(value) > 4096:
        return None
    return value


def _urls_from(value: Any) -> list[str]:
    """Recursively collect HTTP URLs from a play_addr-like value."""

    found: list[str] = []

    def add(item: Any) -> None:
        url = _normalise_media_url(item)
        if url and url not in found:
            found.append(url)

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 5 or node is None:
            return
        if isinstance(node, str):
            add(node)
            return
        if isinstance(node, list):
            for child in node:
                walk(child, depth + 1)
            return
        if isinstance(node, dict):
            for key in (
                "url_list",
                "urlList",
                "UrlList",
                "urls",
                "play_url",
                "playUrl",
                "url",
            ):
                if key in node:
                    walk(node[key], depth + 1)
            # A raw uri is only useful when it is already a URL; otherwise it
            # is a CDN id that needs a signed play endpoint (handled for video).
            uri = node.get("uri")
            if isinstance(uri, str) and uri.startswith(("http://", "https://")):
                add(uri)

    walk(value)
    return found


def _music_urls(music: Any) -> list[str]:
    if not isinstance(music, dict):
        return []
    urls: list[str] = []
    for key in ("play_url", "play_url_v2", "h5_play_url", "audio_url", "url_list"):
        for url in _urls_from(music.get(key)):
            if url not in urls:
                urls.append(url)
    return urls


def _video_urls(video: Any) -> list[str]:
    if not isinstance(video, dict):
        return []

    urls: list[str] = []
    address_keys = (
        "play_addr",
        "play_addr_h264",
        "play_addr_265",
        "play_addr_bytevc1",
        "download_addr",
        "download_addr_h264",
    )
    for key in address_keys:
        addr = video.get(key)
        for url in _urls_from(addr):
            if url not in urls:
                urls.append(url)

        # Older/rare payloads only expose a video URI. The public play endpoint
        # still accepts it when called with a douyin.com Referer.
        if isinstance(addr, dict):
            uri = addr.get("uri") or addr.get("video_id")
            if isinstance(uri, str) and not uri.startswith(("http://", "https://")):
                endpoint = (
                    "https://www.douyin.com/aweme/v1/play/"
                    f"?video_id={quote(uri, safe='')}&ratio=1080p&line=0"
                )
                if endpoint not in urls:
                    urls.append(endpoint)

    bit_rates = video.get("bit_rate") or video.get("bitRate") or []
    if isinstance(bit_rates, list):
        for item in bit_rates:
            if not isinstance(item, dict):
                continue
            for url in _urls_from(item.get("play_addr")):
                if url not in urls:
                    urls.append(url)
    return urls


def _looks_like_aweme(node: Any) -> bool:
    if not isinstance(node, dict) or not node.get("aweme_id"):
        return False
    return any(
        node.get(key) is not None for key in ("music", "video", "images", "desc", "image_post_info")
    )


def _score_aweme(node: dict[str, Any], target_id: str | None) -> int:
    aweme_id = str(node.get("aweme_id") or "")
    score = 0
    if target_id and aweme_id == target_id:
        score += 1000
    if isinstance(node.get("music"), dict):
        score += 30
    if isinstance(node.get("video"), dict):
        score += 20
    images = node.get("images")
    if isinstance(images, list) and images:
        score += 25
    if node.get("desc"):
        score += 5
    if isinstance(node.get("author"), dict):
        score += 3
    return score


def _iter_item_lists(node: Any, depth: int = 0) -> Iterator[list[Any]]:
    if depth > 30:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"item_list", "itemList", "aweme_list", "awemeList"}:
                if isinstance(value, list):
                    yield value
            if isinstance(value, (dict, list)):
                yield from _iter_item_lists(value, depth + 1)
    elif isinstance(node, list):
        for child in node:
            if isinstance(child, (dict, list)):
                yield from _iter_item_lists(child, depth + 1)


def _select_aweme(data: Any, target_id: str | None) -> dict[str, Any] | None:
    """Pick the target aweme, preferring explicit item lists."""

    item_lists = list(_iter_item_lists(data))

    # A known target id is authoritative: never silently download a related
    # video from the recommendation sidebar.
    if target_id:
        for items in item_lists:
            for item in items:
                if isinstance(item, dict) and str(item.get("aweme_id") or "") == target_id:
                    return item
        for item in _walk_awemes(data):
            if str(item.get("aweme_id") or "") == target_id:
                return item
        return None

    for items in item_lists:
        for item in items:
            if _looks_like_aweme(item):
                return item

    best = None
    best_score = -1
    for item in _walk_awemes(data):
        score = _score_aweme(item, target_id)
        if score > best_score:
            best, best_score = item, score
    return best


def _walk_awemes(data: Any, max_nodes: int = 300_000) -> Iterator[dict[str, Any]]:
    stack = [data]
    seen = 0
    while stack and seen < max_nodes:
        node = stack.pop()
        seen += 1
        if isinstance(node, dict):
            if _looks_like_aweme(node):
                yield node
            for value in node.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            for value in node:
                if isinstance(value, (dict, list)):
                    stack.append(value)


def _balanced_json(text: str, start: int) -> str | None:
    """Return the JSON object starting at ``start`` (quote/escape aware)."""

    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _js_json_loads(raw: str) -> Any | None:
    candidates = [raw, html_lib.unescape(raw)]
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    # Some SSR payloads contain the literal ``undefined``.
    sanitised = re.sub(r"\bundefined\b", "null", raw)
    try:
        return json.loads(sanitised)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _assignment_payloads(text: str, marker: str) -> Iterator[str]:
    search_from = 0
    while True:
        marker_at = text.find(marker, search_from)
        if marker_at < 0:
            return
        brace = text.find("{", marker_at + len(marker), marker_at + len(marker) + 300)
        search_from = marker_at + len(marker)
        if brace < 0:
            continue
        raw = _balanced_json(text, brace)
        if raw:
            yield raw


def _script_payloads(text: str) -> Iterator[str]:
    script_re = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
    for match in script_re.finditer(text):
        attrs = match.group(1) or ""
        body = match.group(2).strip()
        if not body:
            continue
        if "RENDER_DATA" in attrs or "__NEXT_DATA__" in attrs:
            body = unquote(html_lib.unescape(body))
            if body.startswith("{"):
                yield body
        elif "application/json" in attrs.lower() or body.startswith("{") and '"aweme_id"' in body:
            yield body


def _iter_json_payloads(page_html: str) -> Iterator[str]:
    seen: set[str] = set()

    def unique(raw: str) -> Iterator[str]:
        digest = hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()
        if digest not in seen:
            seen.add(digest)
            yield raw

    for marker in (
        "window._ROUTER_DATA",
        "_ROUTER_DATA",
        "window.__INITIAL_STATE__",
        "__INITIAL_STATE__",
        "window._SSR_DATA",
        "_SSR_DATA",
        "window._STORE_DATA",
        "window.SIGI_STATE",
        "SIGI_STATE",
    ):
        for raw in _assignment_payloads(page_html, marker):
            yield from unique(raw)

    for raw in _script_payloads(page_html):
        yield from unique(raw)


def parse_page_html(page_html: str, target_id: str | None = None) -> dict[str, Any] | None:
    """Extract the relevant aweme object from a Douyin HTML page."""

    for raw in _iter_json_payloads(page_html):
        data = _js_json_loads(raw)
        if data is None:
            continue
        aweme = _select_aweme(data, target_id)
        if aweme:
            return aweme
    return None


def parse_json_payload(data: Any, target_id: str | None = None) -> dict[str, Any] | None:
    return _select_aweme(data, target_id)


def guess_aweme_id_from_html(page_html: str) -> str | None:
    match = re.search(r'"aweme_id"\s*:\s*"?(\d{5,30})"?', page_html)
    return match.group(1) if match else None


def _clean_text(value: Any, fallback: str, limit: int) -> str:
    if value is None:
        return fallback
    text = html_lib.unescape(str(value))
    text = text.replace("\u200b", "").replace("\x00", "")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return fallback
    return text[:limit]


def _detect_media_type(aweme: dict[str, Any], final_url: str) -> str:
    images = aweme.get("images")
    if isinstance(images, list) and images:
        return "image"
    if isinstance(aweme.get("image_post_info"), dict):
        return "image"
    try:
        aweme_type = int(aweme.get("aweme_type"))
    except (TypeError, ValueError):
        aweme_type = -1
    if aweme_type == 68:
        return "image"
    if "/note/" in urlsplit(final_url).path or "/slides/" in urlsplit(final_url).path:
        return "image"
    return "video"


def _build_parsed(
    aweme: dict[str, Any],
    *,
    source_url: str,
    final_url: str,
    target_id: str | None,
    referer: str | None = None,
    origin: str | None = None,
) -> ParsedMedia:
    aweme_id = str(aweme.get("aweme_id") or target_id or "").strip()
    if not _AWEME_ID_RE.match(aweme_id):
        raise ParseError("未能识别作品 ID，抖音页面结构可能已变化")

    music = aweme.get("music") if isinstance(aweme.get("music"), dict) else {}
    video = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
    author_data = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
    music_author = music.get("author") if isinstance(music.get("author"), dict) else {}

    title = _clean_text(
        aweme.get("desc") or aweme.get("caption") or music.get("title"),
        f"抖音音频 {aweme_id}",
        300,
    )
    author = _clean_text(
        author_data.get("nickname")
        or author_data.get("unique_id")
        or author_data.get("short_id")
        or music_author.get("nickname"),
        "未知作者",
        100,
    )

    media_type = _detect_media_type(aweme, final_url)
    music_urls = _music_urls(music)
    video_urls = _video_urls(video)

    duration = None
    if media_type == "image":
        duration = _duration_seconds(music.get("duration")) or _duration_seconds(
            video.get("duration")
        )
    else:
        duration = _duration_seconds(video.get("duration")) or _duration_seconds(
            music.get("duration")
        )

    if not music_urls and not video_urls:
        raise NoAudioError("未找到可用的音频或视频地址，作品可能已删除、设为私密，或页面结构已变化")

    return ParsedMedia(
        platform="douyin",
        external_id=aweme_id,
        title=title,
        author=author,
        source_url=source_url,
        final_url=final_url,
        media_type=media_type,
        music_urls=tuple(music_urls),
        video_urls=tuple(video_urls),
        duration_seconds=duration,
        referer=referer,
        origin=origin,
    )


class DouyinProvider(BaseProvider):
    name = "douyin"
    display_name = "抖音"

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
            return extract_douyin_url(share_text)
        except AppError:
            return None

    def matches(self, url: str) -> bool:
        try:
            return is_douyin_host(_normalise_host(urlsplit(url).hostname))
        except ValueError:
            return False

    async def parse(self, share_url: str) -> ParsedMedia:
        return await self.parse_share(share_url)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            "Referer": self._settings.douyin_referer,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }

    async def parse_share(self, share_url: str) -> ParsedMedia:
        logger.info("解析抖音链接: %s", _safe_url(share_url))
        page_html, final_url = await self._fetch_page(share_url)
        target_id = extract_aweme_id_from_url(final_url) or guess_aweme_id_from_html(page_html)
        logger.info("短链最终地址=%s, 目标作品=%s", _safe_url(final_url), target_id)

        aweme = parse_page_html(page_html, target_id)

        if aweme is None and target_id:
            logger.warning("页面未包含可解析 JSON，尝试公开 iteminfo 接口")
            aweme = await self._fetch_iteminfo(target_id)

        if aweme is None and target_id:
            logger.warning("iteminfo 未返回数据，尝试备用 iesdouyin 分享页")
            aweme = await self._try_alternate_share_pages(target_id)

        if aweme is None:
            lowered = page_html[:30000].lower()
            challenge = any(
                token in lowered for token in ("captcha", "verify", "安全验证", "访问过于频繁")
            )
            detail = "抖音返回了验证/风控页面" if challenge else None
            raise ParseError(
                "未能从抖音页面解析作品信息，页面结构可能已变化，或链接已失效",
                detail=detail,
            )

        try:
            parsed = _build_parsed(
                aweme,
                source_url=share_url,
                final_url=final_url,
                target_id=target_id,
                referer=self._settings.douyin_referer,
                origin="https://www.douyin.com",
            )
        except NoAudioError:
            # The page object can exist while its play URLs are omitted. The
            # item-info endpoint sometimes still has them.
            if not target_id:
                raise
            logger.warning("页面对象里没有音频地址，尝试 iteminfo 补充解析")
            fallback_aweme = await self._fetch_iteminfo(target_id)
            if fallback_aweme is None:
                fallback_aweme = await self._try_alternate_share_pages(target_id)
            if fallback_aweme is None:
                raise
            parsed = _build_parsed(
                fallback_aweme,
                source_url=share_url,
                final_url=final_url,
                target_id=target_id,
                referer=self._settings.douyin_referer,
                origin="https://www.douyin.com",
            )
        logger.info(
            "解析成功: aweme_id=%s type=%s music=%d video=%d duration=%s",
            parsed.aweme_id,
            parsed.media_type,
            len(parsed.music_urls),
            len(parsed.video_urls),
            parsed.duration_seconds,
        )
        return parsed

    async def _fetch_page(self, url: str) -> tuple[str, str]:
        current = url
        for _hop in range(9):
            validate_douyin_url(current)
            await self._validator.validate(current)
            try:
                async with self._http.stream(
                    "GET",
                    current,
                    headers=self.headers,
                    timeout=self._settings.page_timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("抖音跳转响应缺少 Location 头")
                        current = urljoin(str(response.url), location)
                        logger.info("跟随跳转 -> %s", _safe_url(current))
                        continue

                    if response.status_code != 200:
                        message = "抖音页面请求失败"
                        if response.status_code in (403, 412):
                            message = "抖音拒绝访问（403），可能触发了风控，请稍后重试"
                        elif response.status_code == 404:
                            message = "抖音作品不存在或链接已失效（404）"
                        raise FetchError(
                            message,
                            detail=f"upstream status={response.status_code}",
                        )

                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._settings.max_page_bytes:
                            raise FetchError("抖音页面体积异常，已中止解析")
                    encoding = response.encoding or "utf-8"
                    text = body.decode(encoding, errors="replace")
                    return text, str(response.url)
            except httpx.TimeoutException as exc:
                raise FetchError("请求抖音页面超时，请稍后重试") from exc
            except httpx.RequestError as exc:
                raise FetchError(
                    "无法连接抖音服务器，请检查服务器网络",
                    detail=str(exc),
                ) from exc
        raise FetchError("抖音短链跳转次数过多，已中止")

    async def _fetch_iteminfo(self, aweme_id: str) -> dict[str, Any] | None:
        endpoints = (
            "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/"
            f"?item_ids={quote(aweme_id, safe='')}",
            "https://www.iesdouyin.com/aweme/v1/web/aweme/detail/"
            f"?aweme_id={quote(aweme_id, safe='')}&device_platform=webapp&aid=6383",
        )
        headers = dict(self.headers)
        headers["Accept"] = "application/json, text/plain, */*"
        for endpoint in endpoints:
            try:
                validate_douyin_url(endpoint)
                await self._validator.validate(endpoint)
                async with self._http.stream(
                    "GET",
                    endpoint,
                    headers=headers,
                    timeout=self._settings.page_timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        logger.debug("iteminfo 接口 %s 返回 %s", endpoint, response.status_code)
                        continue
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._settings.max_page_bytes:
                            break
                    data = _js_json_loads(body.decode("utf-8", errors="replace"))
                    if data is None:
                        continue
                    aweme = parse_json_payload(data, aweme_id)
                    if aweme:
                        logger.info("iteminfo 接口解析成功: %s", aweme_id)
                        return aweme
            except (AppError, httpx.HTTPError) as exc:
                logger.debug("iteminfo 接口失败: %s", exc)
        return None

    async def _try_alternate_share_pages(self, aweme_id: str) -> dict[str, Any] | None:
        for kind in ("video", "note"):
            alt = f"https://www.iesdouyin.com/share/{kind}/{aweme_id}/?region=CN&mid=0"
            try:
                page_html, final_url = await self._fetch_page(alt)
                aweme = parse_page_html(page_html, aweme_id)
                if aweme:
                    logger.info("备用分享页解析成功: %s", _safe_url(final_url))
                    return aweme
            except AppError as exc:
                logger.debug("备用分享页 %s 失败: %s", alt, exc)
        return None


def _safe_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
