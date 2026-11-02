"""Provider contract shared by every source platform."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedMedia:
    """Platform-neutral description of one downloadable media item."""

    platform: str
    external_id: str
    title: str
    author: str
    source_url: str
    final_url: str
    media_type: str  # image | video
    music_urls: tuple[str, ...]
    video_urls: tuple[str, ...]
    duration_seconds: float | None = None
    referer: str | None = None
    origin: str | None = None
    media_ports: tuple[int, ...] = ()

    @property
    def media_id(self) -> str:
        return self.external_id

    @property
    def aweme_id(self) -> str:
        """Backward-compatible alias for the original Douyin-only schema."""

        return self.external_id


class BaseProvider(ABC):
    name: str
    display_name: str

    @abstractmethod
    def extract_url(self, share_text: str) -> str | None:
        """Return a supported URL if this provider recognizes one in the text."""

    @abstractmethod
    def matches(self, url: str) -> bool:
        """Return whether this provider owns a URL, without network access."""

    @abstractmethod
    async def parse(self, url: str) -> ParsedMedia:
        """Fetch and parse a URL into a ParsedMedia."""
