"""Tests for improve_blog_post — the 'enhance an existing draft' workflow.

All run in stub mode (no model calls, no network).
"""

from __future__ import annotations

from typing import Any

import pytest

from blog_writer.config import load_config
from blog_writer.workflows import build_review_report, improve_blog_post
from blog_writer.workflows.blog_pipeline import _draft_queries, _draft_title

SAMPLE_DRAFT = """# Securing Agentic AI on Azure

Agents act, so the foundation matters more than the model.

## Identity before infrastructure

Managed identities replace secrets for agents.

## Private networking

Use private endpoints for every dependency.

## Sources
"""


# -----------------------------------------------------------------------------
# Query/title derivation helpers
# -----------------------------------------------------------------------------


def test_draft_title_reads_first_h1() -> None:
    assert _draft_title(SAMPLE_DRAFT) == "Securing Agentic AI on Azure"
    assert _draft_title("no heading here") == ""


def test_draft_queries_use_title_then_headings_and_skip_boilerplate() -> None:
    queries = _draft_queries(SAMPLE_DRAFT)
    assert queries[0] == "Securing Agentic AI on Azure"
    # Topical headings are folded in, "Sources" is skipped.
    assert any("Identity before infrastructure" in q for q in queries)
    assert any("Private networking" in q for q in queries)
    assert all("Sources" not in q for q in queries)


# -----------------------------------------------------------------------------
# improve_blog_post (workflow-level, stub mode)
# -----------------------------------------------------------------------------


async def test_improve_rejects_empty_draft() -> None:
    config = load_config(stub=True)
    with pytest.raises(ValueError, match="empty"):
        await improve_blog_post("   ", config=config)


async def test_improve_finds_sources_and_rewrites() -> None:
    config = load_config(stub=True)
    state = await improve_blog_post(SAMPLE_DRAFT, config=config)

    # Sources were gathered (stub returns canned in-scope Learn + external hits).
    assert state.internal_hits, "expected at least one MS Learn source"
    assert state.external_hits, "expected at least one external source"
    # A recommendation pass ran.
    assert state.latest_critic is not None
    # The draft was rewritten (writer stage ran).
    assert state.iteration == 1
    assert state.draft


async def test_improve_recommend_only_skips_rewrite() -> None:
    config = load_config(stub=True)
    events: list[dict[str, Any]] = []
    state = await improve_blog_post(
        SAMPLE_DRAFT,
        config=config,
        rewrite=False,
        on_event=events.append,
    )

    # No rewrite: the draft is untouched and iteration stays at 0.
    assert state.iteration == 0
    assert state.draft == SAMPLE_DRAFT
    # But we still produced recommendations + sources.
    assert state.latest_critic is not None
    assert state.internal_hits
    stages = {e.get("stage") for e in events if e["type"] == "stage_start"}
    assert "writer" not in stages
    assert "critic" in stages


async def test_improve_emits_structured_events() -> None:
    config = load_config(stub=True)
    events: list[dict[str, Any]] = []
    await improve_blog_post(SAMPLE_DRAFT, config=config, on_event=events.append)
    types = [e["type"] for e in events]
    for required in ("stage_start", "stage_end", "citations", "recommendations", "draft", "done"):
        assert required in types, f"missing event type: {required}"
    done = next(e for e in events if e["type"] == "done")
    assert done["final_verdict"] == "improved"


async def test_build_review_report_has_key_sections() -> None:
    config = load_config(stub=True)
    state = await improve_blog_post(SAMPLE_DRAFT, config=config, rewrite=False)
    report = build_review_report(state)
    assert "## Recommended improvements" in report
    assert "## Sources found" in report
    assert "### Microsoft Learn" in report
