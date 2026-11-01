"""Filesystem safety helpers."""

from __future__ import annotations

from pathlib import Path


def resolve_music_file(music_dir: Path, relative_path: str | None) -> Path | None:
    """Resolve a DB-stored filename inside MUSIC_DIR, rejecting traversal.

    Only direct children of MUSIC_DIR are accepted. The database stores a bare
    filename; this check is defence in depth against a corrupted row.
    """

    if not relative_path:
        return None
    base = music_dir.resolve()
    try:
        candidate = (base / relative_path).resolve()
    except (OSError, ValueError):
        return None
    if candidate.parent != base:
        return None
    return candidate
