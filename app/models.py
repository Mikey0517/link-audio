"""Pydantic request/response models used by the API and OpenAPI docs."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ExtractRequest(BaseModel):
    job_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="客户端生成的进度任务 ID",
    )
    share_text: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="整段分享文案，或其中任意一个受支持平台的链接",
        examples=["8.25 复制打开抖音，看看【某某的作品】... https://v.douyin.com/xxxxxx/ ..."],
    )

    @field_validator("share_text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("分享文案不能为空")
        return value


class AudioOut(BaseModel):
    id: int
    platform: str
    aweme_id: str
    title: str
    author: str
    source_url: str
    audio_url: str
    file_size: int
    duration_seconds: float | None = None
    created_at: str
    status: str
    media_type: str
    source_kind: str


class ProgressOut(BaseModel):
    job_id: str
    percent: int
    stage: str
    status: str
    error: str | None = None
    duplicate: bool = False
    result: AudioOut | None = None


class ExtractStartedOut(BaseModel):
    success: bool = True
    status: str = "running"
    job_id: str


class AudioListOut(BaseModel):
    items: list[AudioOut]
    total: int


class ExtractResponse(BaseModel):
    success: bool = True
    duplicate: bool = False
    data: AudioOut


class DeleteResponse(BaseModel):
    success: bool = True
    id: int
    message: str


class HealthOut(BaseModel):
    status: str
    version: str
    database: str
    music_dir: str
    music_dir_writable: bool
    ffmpeg_available: bool
    ffmpeg_path: str | None = None
    ffprobe_available: bool
    active_extractions: int
    max_concurrent_extractions: int
    providers: list[str]
