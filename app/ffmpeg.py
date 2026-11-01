"""FFmpeg/ffprobe wrappers (subprocess based, no shell interpolation)."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

from .config import Settings
from .errors import FFmpegError, FFmpegMissingError, MediaTooLargeError

logger = logging.getLogger(__name__)

_URL_QUERY_RE = re.compile(r'(https?://[^\s"\'<>]+?)\?[^\s"\'<>]*')


def is_mp3_file(path: Path) -> bool:
    """Cheap MP3 sniffing: ID3 tag or an MPEG audio frame sync."""

    try:
        with path.open("rb") as file_obj:
            head = file_obj.read(16)
    except OSError:
        return False
    if len(head) < 4:
        return False
    if head.startswith(b"ID3"):
        return True
    return head[0] == 0xFF and (head[1] & 0xE0) == 0xE0


class FFmpegProcessor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ffmpeg = shutil.which(settings.ffmpeg_bin)
        ffprobe_name = settings.ffprobe_bin
        self._ffprobe = shutil.which(ffprobe_name)
        if self._ffprobe is None and self._ffmpeg:
            sibling = Path(self._ffmpeg).with_name(
                "ffprobe" + (".exe" if self._ffmpeg.lower().endswith(".exe") else "")
            )
            if sibling.is_file():
                self._ffprobe = str(sibling)

    @property
    def ffmpeg_available(self) -> bool:
        return bool(self._ffmpeg)

    @property
    def ffprobe_available(self) -> bool:
        return bool(self._ffprobe)

    @property
    def ffmpeg_path(self) -> str | None:
        return self._ffmpeg

    def _require_ffmpeg(self) -> str:
        if not self._ffmpeg:
            raise FFmpegMissingError()
        return self._ffmpeg

    def _network_input_args(
        self,
        url: str,
        referer: str | None,
        origin: str | None,
    ) -> list[str]:
        header_lines: list[str] = []
        if referer:
            header_lines.append(f"Referer: {referer}")
        if origin:
            header_lines.append(f"Origin: {origin}")
        header_lines.append("Accept-Language: zh-CN,zh;q=0.9")
        headers = "\r\n".join(header_lines) + "\r\n"
        return [
            "-user_agent",
            self._settings.user_agent,
            "-headers",
            headers,
            "-rw_timeout",
            "30000000",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-protocol_whitelist",
            "file,http,https,tcp,tls,crypto",
            "-i",
            url,
        ]

    def _output_args(
        self,
        destination: Path,
        *,
        title: str | None,
        artist: str | None,
    ) -> list[str]:
        args = [
            "-vn",
            "-map",
            "0:a:0?",
            "-acodec",
            "libmp3lame",
            "-q:a",
            "2",
            "-id3v2_version",
            "3",
            "-fs",
            str(self._settings.max_download_bytes),
        ]
        if title:
            args += ["-metadata", f"title={_metadata_value(title)}"]
        if artist:
            args += ["-metadata", f"artist={_metadata_value(artist)}"]
        # The temporary file ends in .part, so the output format must be explicit.
        args += ["-f", "mp3", str(destination)]
        return args

    async def extract_audio_from_url(
        self,
        url: str,
        destination: Path,
        *,
        referer: str | None,
        origin: str | None = None,
        title: str | None = None,
        artist: str | None = None,
    ) -> None:
        ffmpeg = self._require_ffmpeg()
        destination.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "warning",
            "-y",
            *self._network_input_args(url, referer, origin),
            *self._output_args(destination, title=title, artist=artist),
        ]
        logger.info("FFmpeg 从视频直链提取音轨: %s", _safe_url(url))
        await self._run_ffmpeg(cmd, destination)

    async def transcode_to_mp3(
        self,
        source: Path,
        destination: Path,
        *,
        title: str | None = None,
        artist: str | None = None,
    ) -> None:
        ffmpeg = self._require_ffmpeg()
        destination.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            str(source),
            *self._output_args(destination, title=title, artist=artist),
        ]
        logger.info("FFmpeg 转码本地媒体文件: %s", source.name)
        await self._run_ffmpeg(cmd, destination)

    async def _run_ffmpeg(self, cmd: list[str], destination: Path) -> None:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self._settings.ffmpeg_timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                destination.unlink(missing_ok=True)
                raise FFmpegError("FFmpeg 处理超时，视频可能过大") from exc

            error_text = _redact_urls(stderr.decode("utf-8", errors="replace").strip())
            if process.returncode != 0:
                destination.unlink(missing_ok=True)
                logger.warning("FFmpeg 失败 (code=%s): %s", process.returncode, error_text[-2000:])
                raise FFmpegError(
                    "FFmpeg 提取音轨失败，视频直链可能已过期",
                    detail=error_text[-1000:] or f"exit code {process.returncode}",
                )

            if not destination.is_file() or destination.stat().st_size == 0:
                destination.unlink(missing_ok=True)
                raise FFmpegError(
                    "FFmpeg 未生成音频文件，视频可能没有音轨",
                    detail=error_text[-1000:],
                )
            size = destination.stat().st_size
            if size >= self._settings.max_download_bytes:
                destination.unlink(missing_ok=True)
                raise MediaTooLargeError(
                    f"提取出的音频超过大小限制（{self._settings.max_download_mb} MB）"
                )
            if error_text:
                logger.debug("FFmpeg stderr: %s", error_text[-1000:])
            logger.info("FFmpeg 输出完成: %s (%d bytes)", destination.name, size)
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

    async def probe_duration(self, path: Path) -> float | None:
        """Return media duration from ffprobe, falling back to ``ffmpeg -i``.

        ffprobe is normally installed with FFmpeg, but some minimal/static
        builds only ship the ffmpeg binary. The fallback keeps the
        "complete mix" heuristic usable there too.
        """

        if self._ffprobe:
            duration = await self._probe_with_ffprobe(path)
            if duration is not None:
                return duration
        if self._ffmpeg:
            return await self._probe_with_ffmpeg(path)
        return None

    async def _probe_with_ffprobe(self, path: Path) -> float | None:
        cmd = [
            self._ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        stdout, _stderr, process = await self._run_capture(cmd, label="ffprobe")
        if process is None or process.returncode != 0:
            return None
        try:
            duration = float(stdout.decode("ascii", "ignore").strip().splitlines()[0])
        except (ValueError, IndexError):
            return None
        if duration <= 0:
            return None
        return round(duration, 3)

    async def _probe_with_ffmpeg(self, path: Path) -> float | None:
        cmd = [self._ffmpeg, "-hide_banner", "-i", str(path)]
        _stdout, stderr, process = await self._run_capture(cmd, label="ffmpeg -i")
        if process is None:
            return None
        text = stderr.decode("utf-8", errors="replace")
        match = re.search(r"Duration:\s*(\d+):(\d+):([0-9.]+)", text)
        if not match:
            return None
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if duration <= 0:
            return None
        return round(duration, 3)

    async def _run_capture(
        self,
        cmd: list[str],
        *,
        label: str,
    ) -> tuple[bytes, bytes, asyncio.subprocess.Process | None]:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            return stdout, stderr, process
        except TimeoutError:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                    await process.communicate()
                except (ProcessLookupError, OSError):
                    pass
            logger.debug("%s 执行超时", label)
        except OSError as exc:
            logger.debug("%s 执行失败: %s", label, exc)
        return b"", b"", None


def _metadata_value(value: str) -> str:
    clean = " ".join(value.replace("\x00", "").split())
    return clean[:300]


def _safe_url(url: str) -> str:
    # Avoid writing signed query strings / tokens to logs.
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _redact_urls(text: str) -> str:
    """Remove signed query strings from FFmpeg diagnostics before logging."""

    return _URL_QUERY_RE.sub(r"\1?<redacted>", text)
