from __future__ import annotations

import pytest

from app.errors import InvalidURLError, UnsupportedDomainError
from app.providers.base import BaseProvider, ParsedMedia
from app.providers.registry import ProviderRegistry


class FakeProvider(BaseProvider):
    name = "fake"
    display_name = "测试平台"

    def extract_url(self, share_text: str) -> str | None:
        if "fake.example" in share_text:
            return "https://fake.example/item/42"
        return None

    def matches(self, url: str) -> bool:
        return url.startswith("https://fake.example/")

    async def parse(self, url: str) -> ParsedMedia:
        return ParsedMedia(
            platform=self.name,
            external_id="42",
            title="测试条目",
            author="测试作者",
            source_url=url,
            final_url=url,
            media_type="video",
            music_urls=("https://cdn.example/a.mp3",),
            video_urls=(),
            duration_seconds=10.0,
        )


@pytest.mark.asyncio
async def test_registry_dispatches_to_provider() -> None:
    registry = ProviderRegistry([FakeProvider()])
    assert registry.names == ["fake"]
    assert registry.display_names == ["测试平台"]
    assert registry.extract_url("分享 https://fake.example/item/42") == (
        "https://fake.example/item/42"
    )

    parsed = await registry.parse("分享 https://fake.example/item/42")
    assert parsed.platform == "fake"
    assert parsed.external_id == "42"
    assert parsed.title == "测试条目"


def test_registry_rejects_unknown_links() -> None:
    registry = ProviderRegistry([FakeProvider()])
    with pytest.raises(InvalidURLError):
        registry.extract_url("https://other.example/item/1")
    with pytest.raises(UnsupportedDomainError):
        registry.provider_for("https://other.example/item/1")
