"""Extraction orchestration: parse -> music URL or FFmpeg fallback -> DB."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .db import Database
from .downloader import HTTPDownloader
from .errors import (
    AppError,
    DownloadError,
    FFmpegError,
    FFmpegMissingError,
    NoAudioError,
)
from .ffmpeg import FFmpegProcessor, is_mp3_file
from .net import RemoteURLValidator
from .providers import ParsedMedia, ProviderRegistry
from .storage import resolve_music_file

logger = logging.getLogger(__name__)

# How many candidate URLs to try per source. Providers usually return several
# CDN mirrors; trying all of them avoids one dead edge node failing the task.
MAX_URL_ATTEMPTS = 4


@dataclass(frozen=True)
class PreparedAudio:
    path: Path
    duration_seconds: float | None
    source_kind: str


class ExtractionService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        providers: ProviderRegistry,
        downloader: HTTPDownloader,
        ffmpeg: FFmpegProcessor,
        validator: RemoteURLValidator | None = None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._providers = providers
        self._downloader = downloader
        self._ffmpeg = ffmpeg
        self._validator = validator

    async def extract(self, share_text: str) -> tuple[dict, bool]:
        """Return ``(record, duplicate)``.

        Parsing happens before a DB row exists. Every failure after that point
        leaves a ``failed`` row with a diagnostic message, as required.
        """

        parsed = await self._providers.parse(share_text)

        existing = await asyncio.to_thread(
            self._db.find_completed_by_media,
            parsed.platform,
            parsed.external_id,
        )
        if existing:
            existing_path = resolve_music_file(self._settings.music_dir, existing.get("file_path"))
            if existing_path and existing_path.is_file() and existing_path.stat().st_size > 0:
                logger.info(
                    "作品已存在，复用记录 id=%s platform=%s media_id=%s",
                    existing["id"],
                    parsed.platform,
                    parsed.external_id,
                )
                return existing, True
            await asyncio.to_thread(
                self._db.mark_failed,
                existing["id"],
                "原音频文件已丢失，重新提取",
            )

        record_id = await asyncio.to_thread(
            self._db.create_processing,
            platform=parsed.platform,
            aweme_id=parsed.external_id,
            title=parsed.title,
            author=parsed.author,
            source_url=parsed.source_url,
            media_type=parsed.media_type,
        )
        logger.info(
            "开始任务 id=%s platform=%s media_id=%s type=%s",
            record_id,
            parsed.platform,
            parsed.external_id,
            parsed.media_type,
        )

        final_path: Path | None = None
        marked_completed = False
        try:
            final_path, source_kind = await self._produce_audio(parsed, record_id)
            size = final_path.stat().st_size
            # Image direct-download paths deliberately never invoke FFmpeg:
            # their duration comes from the parsed music metadata.
            duration = parsed.duration_seconds
            if duration is None or source_kind in {"music_transcoded", "video_ffmpeg"}:
                probed = await self._ffmpeg.probe_duration(final_path)
                duration = probed or duration

            await asyncio.to_thread(
                self._db.mark_completed,
                record_id,
                file_path=final_path.name,
                file_size=size,
                duration_seconds=duration,
                source_kind=source_kind,
            )
            marked_completed = True
            record = await asyncio.to_thread(self._db.get, record_id)
            if record is None:
                raise AppError(
                    "DB_ERROR",
                    "音频已保存，但数据库记录写入异常",
                    500,
                )
            logger.info(
                "任务完成 id=%s platform=%s media_id=%s kind=%s size=%s duration=%s",
                record_id,
                parsed.platform,
                parsed.external_id,
                source_kind,
                size,
                duration,
            )
            return record, False
        except AppError as exc:
            logger.warning(
                "任务失败 id=%s platform=%s media_id=%s code=%s message=%s",
                record_id,
                parsed.platform,
                parsed.external_id,
                exc.code,
                exc.message,
            )
            await self._mark_failed_safe(record_id, f"{exc.code}: {exc.message}")
            if final_path is not None and not marked_completed:
                final_path.unlink(missing_ok=True)
            raise
        except asyncio.CancelledError:
            if final_path is not None and not marked_completed:
                final_path.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "任务发生未预期异常 id=%s platform=%s media_id=%s",
                record_id,
                parsed.platform,
                parsed.external_id,
            )
            await self._mark_failed_safe(
                record_id,
                f"INTERNAL_ERROR: {type(exc).__name__}",
            )
            if final_path is not None and not marked_completed:
                final_path.unlink(missing_ok=True)
            raise AppError(
                "INTERNAL_ERROR",
                "服务器处理音频时发生错误，请查看服务端日志",
                500,
            ) from exc
        finally:
            await asyncio.to_thread(self._cleanup_temps, record_id)

    # ------------------------------------------------------------------
    # Format selection
    # ------------------------------------------------------------------
    async def _produce_audio(
        self,
        parsed: ParsedMedia,
        record_id: int,
    ) -> tuple[Path, str]:
        require_complete_mix = parsed.media_type == "video"
        prepared, music_error = await self._prepare_music(
            parsed,
            record_id,
            require_complete_mix=require_complete_mix,
        )

        if prepared is not None:
            final_path = await self._finalize(prepared, parsed)
            return final_path, prepared.source_kind

        if parsed.video_urls:
            if parsed.media_type == "video":
                logger.info(
                    "作品 %s 的音乐直链不可用或不是完整混音，回退到视频抽轨",
                    parsed.external_id,
                )
            else:
                logger.info(
                    "图文作品 %s 的音乐直链不可用，尝试从视频地址提取音轨",
                    parsed.external_id,
                )
            prepared_video = await self._extract_video(parsed, record_id)
            final_path = await self._finalize(prepared_video, parsed)
            return final_path, prepared_video.source_kind

        if music_error is not None:
            raise music_error
        raise NoAudioError("未找到可用的音频或视频地址，作品可能已删除或设为私密")

    async def _prepare_music(
        self,
        parsed: ParsedMedia,
        record_id: int,
        *,
        require_complete_mix: bool,
    ) -> tuple[PreparedAudio | None, AppError | None]:
        if not parsed.music_urls:
            return None, None

        last_error: AppError | None = None
        for index, url in enumerate(parsed.music_urls[:MAX_URL_ATTEMPTS]):
            temp_path = self._temp_path(record_id, f"music-{index}")
            converted_path: Path | None = None
            try:
                logger.info("下载音乐直链 [%d/%d]", index + 1, len(parsed.music_urls))
                await self._downloader.download(
                    url,
                    temp_path,
                    referer=parsed.referer,
                    origin=parsed.origin,
                    allowed_ports=parsed.media_ports,
                )
                duration = parsed.duration_seconds
                if require_complete_mix:
                    duration = await self._ffmpeg.probe_duration(temp_path)
                    complete, reason = _is_complete_mix(
                        duration,
                        parsed.duration_seconds,
                    )
                    if not complete:
                        logger.info("不采用音乐直链: %s", reason)
                        temp_path.unlink(missing_ok=True)
                        continue

                if is_mp3_file(temp_path):
                    return (
                        PreparedAudio(
                            path=temp_path,
                            duration_seconds=duration or parsed.duration_seconds,
                            source_kind="music",
                        ),
                        None,
                    )

                if not self._ffmpeg.ffmpeg_available:
                    temp_path.unlink(missing_ok=True)
                    last_error = FFmpegMissingError()
                    continue

                # Rare: a provider returned AAC/M4A at the music URL. Convert it so
                # the stored file is always a real .mp3.
                converted_path = self._temp_path(record_id, f"music-converted-{index}")
                await self._ffmpeg.transcode_to_mp3(
                    temp_path,
                    converted_path,
                    title=parsed.title,
                    artist=parsed.author,
                )
                temp_path.unlink(missing_ok=True)
                if not is_mp3_file(converted_path):
                    raise FFmpegError("音乐直链转码后不是有效的 MP3 文件")
                if require_complete_mix:
                    duration = await self._ffmpeg.probe_duration(converted_path) or duration
                return (
                    PreparedAudio(
                        path=converted_path,
                        duration_seconds=duration or parsed.duration_seconds,
                        source_kind="music_transcoded",
                    ),
                    None,
                )
            except AppError as exc:
                last_error = exc
                logger.warning("音乐直链第 %d 个失败: %s", index + 1, exc.message)
                temp_path.unlink(missing_ok=True)
                if converted_path:
                    converted_path.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("音乐直链处理异常")
                last_error = DownloadError("音乐直链处理失败", detail=str(exc))
                temp_path.unlink(missing_ok=True)
                if converted_path:
                    converted_path.unlink(missing_ok=True)
        return None, last_error

    async def _extract_video(
        self,
        parsed: ParsedMedia,
        record_id: int,
    ) -> PreparedAudio:
        if not parsed.video_urls:
            raise NoAudioError("未找到可用的视频地址")
        if not self._ffmpeg.ffmpeg_available:
            raise FFmpegMissingError()

        last_error: AppError | None = None

        # Strategy A: let FFmpeg read the video URL directly. This is the
        # command requested in the project spec and avoids a full video copy.
        for index, url in enumerate(parsed.video_urls[:MAX_URL_ATTEMPTS]):
            output_path = self._temp_path(record_id, f"video-direct-{index}")
            try:
                # FFmpeg performs its own network access, so validate the
                # host before handing it a URL (SSRF defence in depth).
                if self._validator is not None:
                    await self._validator.validate(
                        url,
                        allowed_ports=parsed.media_ports,
                    )
                await self._ffmpeg.extract_audio_from_url(
                    url,
                    output_path,
                    referer=parsed.referer,
                    origin=parsed.origin,
                    title=parsed.title,
                    artist=parsed.author,
                )
                if not is_mp3_file(output_path):
                    raise FFmpegError("视频抽轨输出不是有效的 MP3 文件")
                duration = await self._ffmpeg.probe_duration(output_path)
                return PreparedAudio(
                    path=output_path,
                    duration_seconds=duration or parsed.duration_seconds,
                    source_kind="video_ffmpeg",
                )
            except AppError as exc:
                last_error = exc
                logger.warning("视频直链抽轨第 %d 个失败: %s", index + 1, exc.message)
                output_path.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("视频直链抽轨异常")
                last_error = FFmpegError("视频直链抽轨失败", detail=str(exc))
                output_path.unlink(missing_ok=True)

        # Strategy B: download the video with proper Referer handling, then
        # transcode locally. Some CDNs reject FFmpeg's network client but allow
        # a normal HTTP GET.
        for index, url in enumerate(parsed.video_urls[:MAX_URL_ATTEMPTS]):
            video_temp = self._temp_path(record_id, f"video-download-{index}")
            output_path = self._temp_path(record_id, f"video-local-{index}")
            try:
                logger.info("FFmpeg 直读失败，改为下载视频后本地抽轨 [%d]", index + 1)
                await self._downloader.download(
                    url,
                    video_temp,
                    referer=parsed.referer,
                    origin=parsed.origin,
                    allowed_ports=parsed.media_ports,
                )
                await self._ffmpeg.transcode_to_mp3(
                    video_temp,
                    output_path,
                    title=parsed.title,
                    artist=parsed.author,
                )
                video_temp.unlink(missing_ok=True)
                if not is_mp3_file(output_path):
                    raise FFmpegError("视频转码输出不是有效的 MP3 文件")
                duration = await self._ffmpeg.probe_duration(output_path)
                return PreparedAudio(
                    path=output_path,
                    duration_seconds=duration or parsed.duration_seconds,
                    source_kind="video_ffmpeg",
                )
            except AppError as exc:
                last_error = exc
                logger.warning("视频下载抽轨第 %d 个失败: %s", index + 1, exc.message)
                video_temp.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("视频下载抽轨异常")
                last_error = FFmpegError("视频下载抽轨失败", detail=str(exc))
                video_temp.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)

        if last_error is not None:
            raise last_error
        raise FFmpegError("从视频中提取音轨失败")

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------
    async def _finalize(self, prepared: PreparedAudio, parsed: ParsedMedia) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", parsed.external_id)[:40] or uuid.uuid4().hex
        filename = f"{safe_id}_{uuid.uuid4().hex[:10]}.mp3"
        destination = self._settings.music_dir / filename
        await asyncio.to_thread(os.replace, prepared.path, destination)
        logger.info("音频已保存: %s", destination.name)
        return destination

    def _temp_path(self, record_id: int, suffix: str) -> Path:
        return self._settings.temp_dir / (f"task-{record_id}-{suffix}-{uuid.uuid4().hex[:8]}.part")

    def _cleanup_temps(self, record_id: int) -> None:
        pattern = f"task-{record_id}-*"
        try:
            for path in self._settings.temp_dir.glob(pattern):
                if path.is_file():
                    path.unlink(missing_ok=True)
        except OSError:
            logger.debug("清理临时文件失败: %s", pattern, exc_info=True)

    async def _mark_failed_safe(self, record_id: int, message: str) -> None:
        try:
            await asyncio.to_thread(self._db.mark_failed, record_id, message)
        except Exception:  # noqa: BLE001
            logger.exception("写失败状态到数据库时出错 id=%s", record_id)


def _is_complete_mix(
    audio_duration: float | None,
    video_duration: float | None,
) -> tuple[bool, str]:
    """Heuristic used to decide whether a music URL is the full video mix.

    A music track that is much shorter (background clip) or much longer (full
    song in the music library) than the video is not the complete mix. If the
    audio duration cannot be probed we cannot confirm completeness, so the
    caller falls back to FFmpeg as the spec requests.
    """

    if audio_duration is None:
        return False, "无法确认音乐时长（ffprobe 不可用或文件异常），回退视频抽轨"
    if video_duration is None:
        return True, ""
    tolerance = max(3.0, video_duration * 0.06)
    if audio_duration < video_duration - tolerance:
        return (
            False,
            f"音乐时长 {audio_duration:.1f}s 明显短于视频 {video_duration:.1f}s",
        )
    if audio_duration > video_duration + max(5.0, video_duration * 0.1):
        return (
            False,
            f"音乐时长 {audio_duration:.1f}s 明显长于视频 {video_duration:.1f}s",
        )
    return True, ""
