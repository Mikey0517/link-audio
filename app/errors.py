"""Domain exceptions with user-facing Chinese messages."""

from __future__ import annotations


class AppError(Exception):
    """An expected failure that can be shown to the user."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 502,
        *,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.detail = detail

    def as_dict(self) -> dict[str, str]:
        # Keep internal diagnostics (FFmpeg stderr, upstream status, URL
        # fragments) in the server log, never in the browser response.
        return {"code": self.code, "message": self.message}


class InvalidURLError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_URL", message, 422)


class UnsupportedDomainError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("UNSUPPORTED_DOMAIN", message, 422)


class UnsafeURLError(AppError):
    def __init__(self, message: str = "该地址不被允许访问") -> None:
        super().__init__("UNSAFE_URL", message, 400)


class FetchError(AppError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__("FETCH_FAILED", message, 502, detail=detail)


class ParseError(AppError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__("PARSE_FAILED", message, 422, detail=detail)


class NoAudioError(AppError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__("NO_AUDIO", message, 422, detail=detail)


class DownloadError(AppError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__("DOWNLOAD_FAILED", message, 502, detail=detail)


class MediaTooLargeError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("FILE_TOO_LARGE", message, 413)


class FFmpegError(AppError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__("FFMPEG_FAILED", message, 502, detail=detail)


class FFmpegMissingError(AppError):
    def __init__(self) -> None:
        super().__init__(
            "FFMPEG_MISSING",
            "服务器未安装 FFmpeg，无法从视频中提取音轨。请安装 FFmpeg 后重试。",
            503,
        )
