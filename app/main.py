"""FastAPI application: API, Range-capable audio streaming and static UI."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email.utils import formatdate
from pathlib import Path

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .audio_service import ExtractionService
from .config import Settings
from .db import Database
from .downloader import HTTPDownloader
from .errors import AppError
from .ffmpeg import FFmpegProcessor
from .logging_conf import configure_logging
from .models import (
    AudioListOut,
    AudioOut,
    DeleteResponse,
    ExtractRequest,
    ExtractResponse,
    HealthOut,
    ProgressOut,
)
from .net import RemoteURLValidator
from .progress import ProgressStore
from .providers import BilibiliProvider, DouyinProvider, ProviderRegistry
from .serializers import serialize_audio
from .storage import resolve_music_file

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
AUDIO_CONTENT_TYPE = "audio/mpeg"
STREAM_CHUNK_SIZE = 64 * 1024


class RangeNotSatisfiable(Exception):
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    settings.prepare_directories()

    db = Database(settings.db_path)
    await asyncio.to_thread(db.initialize)
    recovered = await asyncio.to_thread(db.recover_interrupted)
    if recovered:
        logger.warning("已将 %d 条中断的 processing 记录标记为 failed", recovered)

    client = httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(
            connect=10.0,
            read=settings.page_timeout_seconds,
            write=30.0,
            pool=10.0,
        ),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    validator = RemoteURLValidator(settings)
    providers = ProviderRegistry(
        [
            DouyinProvider(settings, client, validator),
            BilibiliProvider(settings, client, validator),
        ]
    )
    downloader = HTTPDownloader(settings, client, validator)
    ffmpeg = FFmpegProcessor(settings)
    service = ExtractionService(settings, db, providers, downloader, ffmpeg, validator)

    app.state.db = db
    app.state.http = client
    app.state.ffmpeg = ffmpeg
    app.state.providers = providers
    app.state.service = service
    app.state.progress = ProgressStore()
    app.state.semaphore = asyncio.Semaphore(settings.max_concurrent_extractions)
    app.state.active_extractions = 0

    logger.info(
        "启动完成: MUSIC_DIR=%s DB_PATH=%s 并发=%d 平台=%s",
        settings.music_dir,
        settings.db_path,
        settings.max_concurrent_extractions,
        ",".join(providers.display_names),
    )
    if not ffmpeg.ffmpeg_available:
        logger.warning(
            "未找到 FFmpeg（%s）。图文直链仍可下载，视频作品无法抽轨。",
            settings.ffmpeg_bin,
        )
    if not ffmpeg.ffprobe_available:
        logger.warning(
            "未找到 ffprobe（%s），时长探测将回退到 ffmpeg -i。",
            settings.ffprobe_bin,
        )

    try:
        yield
    finally:
        await client.aclose()
        logger.info("服务已停止")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="链接音频库",
        description="把支持平台的分享链接中的音频保存到本地，供自用车机浏览器播放。",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("%s %s -> unhandled exception", request.method, request.url.path)
            raise
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        elapsed_ms = (time.perf_counter() - started) * 1000
        if request.url.path not in {"/health"} and not request.url.path.startswith("/static/"):
            logger.info(
                "%s %s -> %s %.1fms client=%s id=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                request.client.host if request.client else "-",
                request_id,
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        logger.warning("请求参数校验失败: %s", exc.errors()[:3])
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数不合法：分享文案不能为空，且不能超过 8000 个字符",
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": detail},
                headers=exc.headers,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail},
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_exception_handler(request: Request, exc: Exception):
        logger.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务器内部错误，请查看服务端日志",
                }
            },
        )

    @app.post("/api/extract", response_model=ExtractResponse)
    async def extract_audio(payload: ExtractRequest, request: Request):
        settings: Settings = request.app.state.settings
        semaphore: asyncio.Semaphore = request.app.state.semaphore
        service: ExtractionService = request.app.state.service

        if settings.queue_timeout_seconds <= 0:
            if semaphore.locked():
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "BUSY",
                        "message": "当前提取任务已满，请稍后重试",
                    },
                )
            await semaphore.acquire()
        else:
            try:
                await asyncio.wait_for(
                    semaphore.acquire(),
                    timeout=settings.queue_timeout_seconds,
                )
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "BUSY",
                        "message": "服务器提取任务繁忙，请稍后重试",
                    },
                ) from exc

        request.app.state.active_extractions += 1
        job = None
        if payload.job_id:
            job = request.app.state.progress.create(payload.job_id)
            job.report(1, "准备提取")
        try:
            record, duplicate = await service.extract(
                payload.share_text,
                progress=job.report if job is not None else None,
            )
            data = serialize_audio(record)
            if job is not None:
                job.complete()
            return {
                "success": True,
                "duplicate": duplicate,
                "data": data,
            }
        except AppError as exc:
            if job is not None:
                job.fail(exc.code, exc.message)
            logger.warning("提取失败: code=%s message=%s", exc.code, exc.message)
            raise HTTPException(
                status_code=exc.http_status,
                detail=exc.as_dict(),
            ) from exc
        except Exception as exc:  # noqa: BLE001
            if job is not None:
                job.fail("INTERNAL_ERROR", "服务器处理失败")
            logger.exception("提取接口异常")
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "INTERNAL_ERROR",
                    "message": "服务器处理失败，请查看服务端日志",
                },
            ) from exc
        finally:
            request.app.state.active_extractions = max(0, request.app.state.active_extractions - 1)
            semaphore.release()

    @app.get("/api/extract/progress/{job_id}", response_model=ProgressOut)
    async def extract_progress(job_id: str, request: Request):
        job = request.app.state.progress.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "PROGRESS_NOT_FOUND", "message": "进度任务不存在或已过期"},
            )
        return job.snapshot()

    @app.get("/api/audios", response_model=AudioListOut)
    async def list_audios(request: Request):
        db: Database = request.app.state.db
        records = await asyncio.to_thread(db.list_completed)
        items: list[AudioOut] = [AudioOut(**serialize_audio(r)) for r in records]
        return {"items": items, "total": len(items)}

    @app.delete("/api/audios/{audio_id}", response_model=DeleteResponse)
    async def delete_audio(audio_id: int, request: Request):
        settings: Settings = request.app.state.settings
        db: Database = request.app.state.db
        record = await asyncio.to_thread(db.get, audio_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": "音频记录不存在"},
            )

        file_path = resolve_music_file(settings.music_dir, record.get("file_path"))
        if file_path is not None:
            try:
                await asyncio.to_thread(file_path.unlink, missing_ok=True)
            except OSError as exc:
                logger.exception("删除音频文件失败: %s", file_path)
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "DELETE_FAILED",
                        "message": "删除磁盘文件失败，请检查文件权限",
                    },
                ) from exc
        elif record.get("file_path"):
            logger.warning("数据库中的文件路径不安全，已拒绝删除: %r", record.get("file_path"))

        deleted = await asyncio.to_thread(db.delete, audio_id)
        if deleted is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": "音频记录不存在"},
            )
        logger.info("已删除音频 id=%s title=%s", audio_id, record.get("title"))
        return {"success": True, "id": audio_id, "message": "已删除"}

    @app.api_route("/audio/{audio_id}", methods=["GET", "HEAD"])
    async def stream_audio(audio_id: int, request: Request):
        return await _audio_response(audio_id, request)

    @app.get("/health", response_model=HealthOut)
    async def health(request: Request):
        settings: Settings = request.app.state.settings
        db: Database = request.app.state.db
        ffmpeg: FFmpegProcessor = request.app.state.ffmpeg
        db_ok = await asyncio.to_thread(db.ping)
        writable = await asyncio.to_thread(_dir_writable, settings.music_dir)
        ffmpeg_ok = ffmpeg.ffmpeg_available
        status = "ok" if db_ok and writable and ffmpeg_ok else "degraded"
        return {
            "status": status,
            "version": __version__,
            "database": "ok" if db_ok else "error",
            "music_dir": str(settings.music_dir),
            "music_dir_writable": writable,
            "ffmpeg_available": ffmpeg_ok,
            "ffmpeg_path": ffmpeg.ffmpeg_path,
            "ffprobe_available": ffmpeg.ffprobe_available,
            "active_extractions": int(request.app.state.active_extractions),
            "max_concurrent_extractions": settings.max_concurrent_extractions,
            "providers": list(request.app.state.providers.names),
        }

    @app.get("/", include_in_schema=False)
    async def extract_page():
        return FileResponse(
            STATIC_DIR / "index.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/library", include_in_schema=False)
    async def library_page():
        return FileResponse(
            STATIC_DIR / "library.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


async def _audio_response(audio_id: int, request: Request) -> Response:
    settings: Settings = request.app.state.settings
    db: Database = request.app.state.db

    record = await asyncio.to_thread(db.get, audio_id)
    if record is None:
        return _error_response(404, "NOT_FOUND", "音频不存在")
    if record.get("status") != "completed":
        return _error_response(404, "AUDIO_NOT_READY", "该音频尚未提取完成或提取失败")

    path = resolve_music_file(settings.music_dir, record.get("file_path"))
    if path is None or not path.is_file():
        logger.warning("音频文件缺失 id=%s path=%r", audio_id, record.get("file_path"))
        return _error_response(404, "FILE_MISSING", "音频文件不存在，请重新提取")

    stat = await asyncio.to_thread(path.stat)
    total = stat.st_size
    headers = _audio_headers(audio_id, path, stat.st_mtime, total)

    range_header = request.headers.get("range")
    try:
        selected = _parse_range_header(range_header, total)
    except RangeNotSatisfiable:
        return JSONResponse(
            status_code=416,
            content={
                "detail": {
                    "code": "RANGE_NOT_SATISFIABLE",
                    "message": "请求的 Range 超出文件范围",
                }
            },
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes */{total}",
            },
        )

    if selected is None:
        if total == 0:
            headers["Content-Length"] = "0"
            return Response(status_code=200, headers=headers)
        status_code = 200
        start, end = 0, total - 1
    else:
        status_code = 206
        start, end = selected
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        headers["Content-Length"] = str(end - start + 1)

    if request.method.upper() == "HEAD":
        # No body for HEAD, but the same status and headers as GET (including
        # a 206 + Content-Range when a Range header was supplied).
        return Response(status_code=status_code, headers=headers)

    length = end - start + 1
    return StreamingResponse(
        _file_chunks(path, start, length),
        status_code=status_code,
        headers=headers,
    )


async def _file_chunks(path: Path, start: int, length: int) -> AsyncIterator[bytes]:
    remaining = length
    try:
        async with await anyio.open_file(path, "rb") as file_obj:
            await file_obj.seek(start)
            while remaining > 0:
                chunk = await file_obj.read(min(STREAM_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    except asyncio.CancelledError:
        raise
    except OSError:
        logger.exception("读取音频文件失败: %s", path)


def _parse_range_header(value: str | None, total: int) -> tuple[int, int] | None:
    """Parse one RFC 7233 byte range.

    Returns ``None`` when the header is absent or uses an unsupported unit
    (the server then sends the full 200 response). Raises
    :class:`RangeNotSatisfiable` for malformed or unsatisfiable byte ranges.
    """

    if not value or not value.strip():
        return None
    raw = value.strip()
    if not raw.lower().startswith("bytes="):
        return None
    if total <= 0:
        raise RangeNotSatisfiable
    spec = raw[6:].strip()
    if not spec or "," in spec or len(spec) > 200:
        raise RangeNotSatisfiable

    if "-" not in spec:
        raise RangeNotSatisfiable
    start_text, end_text = spec.split("-", 1)
    start_text = start_text.strip()
    end_text = end_text.strip()

    try:
        if start_text == "":
            suffix = int(end_text)
            if suffix <= 0:
                raise RangeNotSatisfiable
            suffix = min(suffix, total)
            return total - suffix, total - 1

        start = int(start_text)
        if start < 0 or start >= total:
            raise RangeNotSatisfiable
        if end_text == "":
            return start, total - 1
        end = int(end_text)
        if end < start:
            raise RangeNotSatisfiable
        return start, min(end, total - 1)
    except ValueError as exc:
        raise RangeNotSatisfiable from exc


def _audio_headers(
    audio_id: int,
    path: Path,
    mtime: float,
    total: int,
) -> dict[str, str]:
    return {
        "Content-Type": AUDIO_CONTENT_TYPE,
        "Accept-Ranges": "bytes",
        "Content-Length": str(total),
        "Cache-Control": "private, no-cache",
        "ETag": f'"{int(mtime * 1000):x}-{total:x}"',
        "Last-Modified": formatdate(mtime, usegmt=True),
        "Content-Disposition": f'inline; filename="audio_{audio_id}.mp3"',
        "X-Content-Type-Options": "nosniff",
    }


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
    )


def _dir_writable(path: Path) -> bool:
    try:
        return path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False


app = create_app()
