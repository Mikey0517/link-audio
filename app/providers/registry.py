"""Provider registry and platform-neutral extraction entry point."""

from __future__ import annotations

from ..errors import InvalidURLError, UnsupportedDomainError
from .base import BaseProvider, ParsedMedia


class ProviderRegistry:
    def __init__(self, providers: list[BaseProvider]) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = providers

    @property
    def providers(self) -> tuple[BaseProvider, ...]:
        return tuple(self._providers)

    @property
    def names(self) -> list[str]:
        return [provider.name for provider in self._providers]

    @property
    def display_names(self) -> list[str]:
        return [provider.display_name for provider in self._providers]

    def extract_url(self, share_text: str) -> str:
        for provider in self._providers:
            url = provider.extract_url(share_text)
            if url:
                return url
        supported = "、".join(self.display_names)
        raise InvalidURLError(f"未找到支持的平台链接，目前支持：{supported}")

    def provider_for(self, url: str) -> BaseProvider:
        for provider in self._providers:
            if provider.matches(url):
                return provider
        supported = "、".join(self.display_names)
        raise UnsupportedDomainError(f"暂不支持该链接，目前支持：{supported}")

    async def parse(self, share_text: str) -> ParsedMedia:
        url = self.extract_url(share_text)
        provider = self.provider_for(url)
        return await provider.parse(url)
