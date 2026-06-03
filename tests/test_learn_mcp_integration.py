"""Live integration test against the MS Learn Docs MCP server.

Gated behind ``RUN_INTEGRATION_TESTS=1`` so unit-test runs (CI, dev) don't
hit the network and fail when offline. Run locally with::

    $env:RUN_INTEGRATION_TESTS = "1"; pytest tests/test_learn_mcp_integration.py -v
"""

from __future__ import annotations

import os

import pytest

from blog_writer.config import AppConfig
from blog_writer.tools.learn_mcp import (
    fetch_learn_page,
    load_learn_scopes,
    search_learn,
    search_learn_code_samples,
)


INTEGRATION_ENABLED = os.getenv("RUN_INTEGRATION_TESTS") == "1"
skipif_no_network = pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason="Live MS Learn MCP test; set RUN_INTEGRATION_TESTS=1 to run.",
)


@skipif_no_network
@pytest.mark.asyncio
async def test_search_learn_returns_in_scope_hits() -> None:
    hits = await search_learn("Azure landing zone for AI workloads")
    assert hits, "expected at least one search hit from the live MCP"

    scope = load_learn_scopes(AppConfig(stub=True, provider="stub"))
    in_scope = scope.filter_hits(hits)
    urls = [h.url for h in hits]
    assert in_scope, f"no in-scope hits among {urls!r}"
    # The CAF / Architecture Center should usually surface for this query.
    assert any(
        "/cloud-adoption-framework/" in h.url
        or "/architecture/" in h.url
        or "/well-architected/" in h.url
        for h in in_scope
    ), f"expected at least one CAF/WAF/Architecture hit, got {[h.url for h in in_scope]}"


@skipif_no_network
@pytest.mark.asyncio
async def test_search_learn_code_samples_returns_python_samples() -> None:
    samples = await search_learn_code_samples(
        "Azure AI Foundry create agent", language="python"
    )
    assert samples, "expected at least one code sample"
    # The MCP server treats language as a soft hint, not a hard filter, so
    # other languages can sneak in. We just want at least one Python sample.
    python_samples = [s for s in samples if s.language.lower().startswith("py")]
    assert python_samples, (
        f"expected at least one python sample; got languages "
        f"{[s.language for s in samples]}"
    )
    # All samples should have a non-trivial code body.
    assert all(len(s.code.strip()) > 20 for s in samples)


@skipif_no_network
@pytest.mark.asyncio
async def test_fetch_learn_page_returns_markdown() -> None:
    md = await fetch_learn_page(
        "https://learn.microsoft.com/azure/cloud-adoption-framework/ready/landing-zone/"
    )
    assert md and isinstance(md, str)
    assert len(md) > 200
    # The page is markdown; we expect at least one heading.
    assert "#" in md
