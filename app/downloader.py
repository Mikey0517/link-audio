"""Streaming HTTP downloads with SSRF, size, redirect and content checks."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import anyio
import httpx

from .config import Settings
from .errors import AppError, DownloadError, MediaTooLargeError
from .net import RemoteURLValidator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size: int
    content_type: str
    final_url: str


class HTTPDownloader:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
        validator: RemoteURLValidator,
    ) -> None:
        self._settings = settings
        self._http = http_client
        self._validator = validator

    def _headers(self, referer: str | None, origin: str | None) -> dict[str, str]:
        headers = {
            "User-Agent": self._settings.user_agent,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            # Avoid transparent gzip decoding changing Content-Length.
            "Accept-Encoding": "identity",
        }
        if referer:
            headers["Referer"] = referer
        if origin:
            headers["Origin"] = origin
        return headers

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        referer: str | None = None,
        origin: str | None = None,
        max_bytes: int | None = None,
    ) -> DownloadResult:
        """Download ``url`` into ``destination`` atomically-ish.

        The caller receives the path only after a complete, non-HTML response.
        Partial files are removed on every failure.
        """

        destination.parent.mkdir(parents=True, exist_ok=True)
        limit = max_bytes or self._settings.max_download_bytes

        try:
            async with asyncio.timeout(self._settings.download_timeout_seconds):
                return await self._download_once(
                    url,
                    destination,
                    referer=referer,
                    origin=origin,
                    max_bytes=limit,
                )
        except TimeoutError as exc:
            destination.unlink(missing_ok=True)
            raise DownloadError("下载超时，请稍后重试") from exc
        except AppError:
            destination.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001 - convert to a user-facing error
            destination.unlink(missing_ok=True)
            logger.exception("下载失败: %s", _safe_url(url))
            raise DownloadError("下载音频失败", detail=str(exc)) from exc

    async def _download_once(
        self,
        url: str,
        destination: Path,
        *,
        referer: str | None,
        origin: str | None,
        max_bytes: int,
    ) -> DownloadResult:
        current = url
        for _hop in range(7):
            await self._validator.validate(current)
            try:
                async with self._http.stream(
                    "GET",
                    current,
                    headers=self._headers(referer, origin),
                    timeout=self._settings.download_timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise DownloadError("音频地址跳转缺少 Location")
                        current = urljoin(str(response.url), location)
                        logger.info("媒体下载跟随跳转 -> %s", _safe_url(current))
                        continue

                    if response.status_code != 200:
                        raise DownloadError(
                            "音频下载失败",
                            detail=f"upstream status={response.status_code}",
                        )

                    content_type = (response.headers.get("content-type") or "").lower()
                    if "text/html" in content_type or "application/json" in content_type:
                        raise DownloadError("音频地址返回了网页或 JSON，可能已过期或被拒绝访问")

                    declared_length = _parse_content_length(response.headers.get("content-length"))
                    if declared_length is not None and declared_length > max_bytes:
                        raise MediaTooLargeError(
                            f"文件超过大小限制（{self._settings.max_download_mb} MB）"
                        )

                    total = 0
                    first_chunk = True
                    async with await anyio.open_file(destination, "wb") as file_obj:
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            if first_chunk:
                                first_chunk = False
                                if _looks_like_html(chunk[:1024]):
                                    raise DownloadError(
                                        "音频地址返回了网页，直链可能已过期或被拒绝访问"
                                    )
                            total += len(chunk)
                            if total > max_bytes:
                                raise MediaTooLargeError(
                                    f"文件超过大小限制（{self._settings.max_download_mb} MB）"
                                )
                            await file_obj.write(chunk)

                    if total == 0:
                        raise DownloadError("下载内容为空")
                    if declared_length is not None and total != declared_length:
                        raise DownloadError("下载不完整，内容长度不一致")

                    logger.info(
                        "下载完成: %s (%d bytes, %s)",
                        _safe_url(current),
                        total,
                        content_type or "unknown",
                    )
                    return DownloadResult(
                        path=destination,
                        size=total,
                        content_type=content_type,
                        final_url=current,
                    )
            except httpx.TimeoutException as exc:
                raise DownloadError("下载音频超时，请稍后重试") from exc
            except httpx.RequestError as exc:
                raise DownloadError(
                    "无法连接音频服务器",
                    detail=str(exc),
                ) from exc
        raise DownloadError("音频地址跳转次数过多")


def _parse_content_length(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _looks_like_html(first_bytes: bytes) -> bool:
    head = first_bytes.lstrip().lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or b"<head>" in head[:512]
        or b"<body" in head[:512]
    )


def _safe_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
