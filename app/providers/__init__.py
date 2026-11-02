"""Pluggable source providers: Douyin, Bilibili and future platforms."""

from .base import BaseProvider, ParsedMedia
from .bilibili import BilibiliProvider, extract_bilibili_url, validate_bilibili_url
from .douyin import DouyinProvider, extract_douyin_url, validate_douyin_url
from .registry import ProviderRegistry

__all__ = [
    "BaseProvider",
    "BilibiliProvider",
    "DouyinProvider",
    "ParsedMedia",
    "ProviderRegistry",
    "extract_bilibili_url",
    "extract_douyin_url",
    "validate_bilibili_url",
    "validate_douyin_url",
]
