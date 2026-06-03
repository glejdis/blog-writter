"""Tests for the Learn Browser MCP server (core + workflow integration)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from blog_writer.tools import bing_search
from blog_writer.tools.learn_mcp import LearnCodeSample, LearnHit, LearnScopeFilter
from mcp_servers.learn_browser import core


@pytest.fixture
def tmp_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """Point the core caches at a per-test temp dir so they never bleed state."""
    from mcp_servers.learn_browser.cache import TTLCache

    monkeypatch.setattr(core, "_fetch_cache", TTLCache(tmp_path / "fetch"))
    monkeypatch.setattr(
        core, "_search_cache", TTLCache(tmp_path / "search", default_ttl_s=3600)
    )
    return tmp_path


async def test_search_all_learn_normalizes_upstream_hits(tmp_cache: Any) -> None:
    upstream_hits = [
        LearnHit(
            title="What is Azure OpenAI",
            url="https://learn.microsoft.com/azure/ai-services/openai/overview",
            excerpt="Overview of Azure OpenAI Service.",
        ),
        LearnHit(
            title="Pricing",
            url="https://learn.microsoft.com/azure/ai-services/openai/pricing",
            excerpt="Pricing info.",
        ),
    ]
    with patch.object(core, "_upstream_search_learn", AsyncMock(return_value=upstream_hits)):
        hits = await core.search_all_learn("azure openai", top_k=8)

    assert len(hits) == 2
    assert hits[0].source == "learn"
    assert hits[0].url.endswith("/openai/overview")
    assert hits[1].snippet == "Pricing info."


async def test_search_all_learn_respects_top_k(tmp_cache: Any) -> None:
    upstream = [
        LearnHit(title=f"hit {i}", url=f"https://learn.microsoft.com/p/{i}", excerpt="")
        for i in range(10)
    ]
    with patch.object(core, "_upstream_search_learn", AsyncMock(return_value=upstream)):
        hits = await core.search_all_learn("q", top_k=3)

    assert len(hits) == 3


async def test_search_all_learn_uses_cache(tmp_cache: Any) -> None:
    upstream = [
        LearnHit(title="cached", url="https://learn.microsoft.com/a", excerpt="x")
    ]
    mock = AsyncMock(return_value=upstream)
    with patch.object(core, "_upstream_search_learn", mock):
        first = await core.search_all_learn("topic", top_k=5)
        second = await core.search_all_learn("topic", top_k=5)

    assert first == second
    assert mock.await_count == 1  # second call served from cache


async def test_search_curated_learn_applies_scope_filter(tmp_cache: Any) -> None:
    upstream = [
        LearnHit(
            title="CAF landing zones",
            url="https://learn.microsoft.com/azure/cloud-adoption-framework/ready/landing-zone/",
            excerpt="",
        ),
        LearnHit(
            title="Unrelated product doc",
            url="https://learn.microsoft.com/azure/some-other-service/overview",
            excerpt="",
        ),
    ]
    scope = LearnScopeFilter(allow_list=("/azure/cloud-adoption-framework/",))
    with patch.object(core, "_upstream_search_learn", AsyncMock(return_value=upstream)):
        hits = await core.search_curated_learn("landing zones", scope=scope)

    assert len(hits) == 1
    assert "cloud-adoption-framework" in hits[0].url


async def test_fetch_learn_page_caches_body(tmp_cache: Any) -> None:
    mock = AsyncMock(return_value="# Page body")
    with patch.object(core, "_upstream_fetch_learn_page", mock):
        a = await core.fetch_learn_page("https://learn.microsoft.com/x")
        b = await core.fetch_learn_page("https://learn.microsoft.com/x")

    assert a == "# Page body"
    assert a == b
    assert mock.await_count == 1


async def test_fetch_learn_page_returns_none_on_failure(tmp_cache: Any) -> None:
    with patch.object(core, "_upstream_fetch_learn_page", AsyncMock(return_value=None)):
        result = await core.fetch_learn_page("https://learn.microsoft.com/missing")

    assert result is None


async def test_search_learn_code_samples_caps_top_k(tmp_cache: Any) -> None:
    upstream = [
        LearnCodeSample(
            description=f"sample {i}",
            code="print(1)",
            url=f"https://learn.microsoft.com/code/{i}",
            language="python",
        )
        for i in range(10)
    ]
    with patch.object(
        core, "_upstream_search_learn_code_samples", AsyncMock(return_value=upstream)
    ):
        samples = await core.search_learn_code_samples("foo", language="python", top_k=4)

    assert len(samples) == 4


async def test_github_search_skipped_without_network(
    tmp_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force httpx to fail; helper must degrade to ``[]`` not raise."""

    class _BoomClient:
        def __init__(self, *a: Any, **kw: Any) -> None: ...
        async def __aenter__(self) -> _BoomClient:
            return self
        async def __aexit__(self, *a: Any) -> None: ...
        async def get(self, *a: Any, **kw: Any) -> Any:
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)
    hits = await core.search_github_azure_samples("agent framework")
    assert hits == []


async def test_search_for_research_combines_learn_and_github(tmp_cache: Any) -> None:
    learn = [
        core.WebHit(title="Learn 1", url="https://learn.microsoft.com/a", snippet="", source="learn"),
    ]
    gh = [
        core.WebHit(title="org/repo", url="https://github.com/org/repo", snippet="", source="github"),
    ]
    with (
        patch.object(core, "search_all_learn", AsyncMock(return_value=learn)),
        patch.object(core, "search_github_azure_samples", AsyncMock(return_value=gh)),
    ):
        hits = await core.search_for_research("q", learn_top_k=5, github_top_k=2)

    assert [h["source"] for h in hits] == ["learn", "github"]


async def test_search_for_research_can_skip_github(tmp_cache: Any) -> None:
    learn = [core.WebHit(title="L", url="https://learn.microsoft.com/x", snippet="", source="learn")]
    gh_mock = AsyncMock(return_value=[])
    with (
        patch.object(core, "search_all_learn", AsyncMock(return_value=learn)),
        patch.object(core, "search_github_azure_samples", gh_mock),
    ):
        hits = await core.search_for_research("q", include_github=False)

    assert len(hits) == 1
    gh_mock.assert_not_awaited()


# -----------------------------------------------------------------------------
# Workflow-facing wrapper
# -----------------------------------------------------------------------------


async def test_bing_search_search_web_delegates_to_mcp_core() -> None:
    fake_raw = [
        {
            "title": "Landing zones",
            "url": "https://learn.microsoft.com/azure/cloud-adoption-framework",
            "snippet": "...",
            "source": "learn",
        }
    ]
    with patch(
        "mcp_servers.learn_browser.core.search_for_research",
        AsyncMock(return_value=fake_raw),
    ):
        hits = await bing_search.search_web("landing zones")

    assert len(hits) == 1
    assert hits[0]["type"] == "learn"
    assert hits[0]["url"].startswith("https://learn.microsoft.com")


async def test_bing_search_search_web_returns_empty_on_failure() -> None:
    with patch(
        "mcp_servers.learn_browser.core.search_for_research",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        hits = await bing_search.search_web("anything")

    assert hits == []
