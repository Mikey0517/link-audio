"""Application configuration loaded from environment variables / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Mobile/15E148 Safari/604.1"
)


class Settings(BaseSettings):
    """Runtime settings.

    Environment variable names are case-insensitive. For example ``MUSIC_DIR``
    maps to :attr:`music_dir`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Storage
    music_dir: Path = Field(default=Path("./music"))
    db_path: Path = Field(default=Path("./data/music.db"))

    # Limits / timeouts
    max_download_mb: int = Field(default=500, ge=1, le=4096)
    max_concurrent_extractions: int = Field(default=2, ge=1, le=16)
    queue_timeout_seconds: float = Field(default=45.0, ge=0, le=600)
    page_timeout_seconds: float = Field(default=20.0, ge=3, le=120)
    bilibili_api_timeout_seconds: float = Field(default=30.0, ge=5, le=180)
    bilibili_api_retries: int = Field(default=2, ge=1, le=4)
    download_timeout_seconds: float = Field(default=300.0, ge=10, le=3600)
    ffmpeg_timeout_seconds: float = Field(default=600.0, ge=10, le=3600)
    max_page_bytes: int = Field(default=8 * 1024 * 1024, ge=64 * 1024)

    # Network
    user_agent: str = DEFAULT_USER_AGENT
    douyin_referer: str = "https://www.douyin.com/"
    ssrf_dns_check: bool = True

    # Tools (override when ffmpeg is not on PATH)
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    # Logging
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        value = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if value not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return value

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024

    @property
    def temp_dir(self) -> Path:
        return self.music_dir / ".tmp"

    def prepare_directories(self) -> None:
        """Create storage directories; safe to call repeatedly."""
        self.music_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
