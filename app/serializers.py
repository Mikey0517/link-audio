"""Convert database rows to the public API shape (never expose disk paths)."""

from __future__ import annotations

from typing import Any


def serialize_audio(record: dict[str, Any]) -> dict[str, Any]:
    file_size = record.get("file_size")
    duration = record.get("duration_seconds")
    return {
        "id": int(record["id"]),
        "platform": str(record.get("platform") or "douyin"),
        "aweme_id": str(record.get("aweme_id") or ""),
        "title": str(record.get("title") or "未命名音频"),
        "author": str(record.get("author") or "未知作者"),
        "source_url": str(record.get("source_url") or ""),
        "audio_url": f"/audio/{int(record['id'])}",
        "file_size": int(file_size) if file_size is not None else 0,
        "duration_seconds": float(duration) if duration is not None else None,
        "created_at": str(record.get("created_at") or ""),
        "status": str(record.get("status") or ""),
        "media_type": str(record.get("media_type") or "video"),
        "source_kind": str(record.get("source_kind") or ""),
    }
