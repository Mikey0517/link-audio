"""Generic URL safety helpers shared by providers and the media downloader."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit

from .config import Settings
from .errors import InvalidURLError, UnsafeURLError


def normalise_host(host: str | None) -> str:
    if not host:
        return ""
    host = host.strip().lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    return host


def validate_http_url(url: str, *, allowed_ports: tuple[int, ...] = ()) -> str:
    """Validate scheme, credentials, host and port for a generic remote URL."""

    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise InvalidURLError("链接格式无效") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeURLError("仅支持 http/https 链接")
    if parsed.username or parsed.password:
        raise UnsafeURLError("链接不能包含用户名或密码")
    if not normalise_host(parsed.hostname):
        raise UnsafeURLError("链接缺少域名")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError("链接端口无效") from exc
    if port not in (None, 80, 443) and port not in allowed_ports:
        raise UnsafeURLError("不支持非标准端口")
    return url


class RemoteURLValidator:
    """Reject non-public destinations and optionally enforce allowed domains."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[str, float] = {}

    async def validate(
        self,
        url: str,
        *,
        allowed_domains: tuple[str, ...] | None = None,
        allowed_ports: tuple[int, ...] = (),
    ) -> str:
        validate_http_url(url, allowed_ports=allowed_ports)
        if allowed_domains is not None:
            host = normalise_host(urlsplit(url).hostname)
            if not any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
                raise UnsafeURLError("该链接的域名不在允许范围内")
        if not self._settings.ssrf_dns_check:
            return url

        host = normalise_host(urlsplit(url).hostname)
        now = time.monotonic()
        cached_until = self._cache.get(host)
        if cached_until and cached_until > now:
            return url

        parsed = urlsplit(url)
        default_port = 80 if parsed.scheme.lower() == "http" else 443
        await self._assert_public_host(host, parsed.port or default_port)
        self._cache[host] = now + 300
        return url

    @staticmethod
    async def _assert_public_host(host: str, port: int) -> None:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None

        if literal is not None:
            addresses = [literal]
        else:
            try:
                loop = asyncio.get_running_loop()
                infos = await asyncio.wait_for(
                    loop.getaddrinfo(
                        host,
                        port,
                        type=socket.SOCK_STREAM,
                        proto=socket.IPPROTO_TCP,
                    ),
                    timeout=5,
                )
            except TimeoutError as exc:
                raise UnsafeURLError(f"域名解析超时 {host}") from exc
            except (socket.gaierror, OSError) as exc:
                raise UnsafeURLError(f"无法解析域名 {host}") from exc
            addresses = []
            for info in infos:
                try:
                    addresses.append(ipaddress.ip_address(info[4][0]))
                except ValueError:
                    continue

        if not addresses:
            raise UnsafeURLError(f"无法解析域名 {host}")
        for address in addresses:
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if not address.is_global:
                raise UnsafeURLError("该链接指向了非公网地址，已拒绝访问")
