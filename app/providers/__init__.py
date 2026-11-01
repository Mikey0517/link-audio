"""Pluggable source providers (Douyin first, more platforms can be added)."""

from .base import BaseProvider, ParsedMedia
from .douyin import DouyinProvider, extract_douyin_url, validate_douyin_url
from .registry import ProviderRegistry

__all__ = [
    "BaseProvider",
    "DouyinProvider",
    "ParsedMedia",
    "ProviderRegistry",
    "extract_douyin_url",
    "validate_douyin_url",
]
