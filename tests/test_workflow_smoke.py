"""End-to-end smoke test in stub mode.

Verifies that the entire pipeline runs without external dependencies, produces
a draft, runs at least one PoC through the sandbox, and the orchestrator
returns a final verdict.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from blog_writer.config import AppConfig
from blog_writer.workflows import run_blog_pipeline


@pytest.mark.asyncio
async def test_pipeline_runs_end_to_end_in_stub_mode(tmp_path: Path) -> None:
    config = AppConfig(
        stub=True,
        provider="stub",
        sandbox="stub",
        max_revisions=1,
        critic_threshold=0,
        drafts_dir=tmp_path / "drafts",
        samples_dir=tmp_path / "samples",
    )
    config.ensure_dirs()

    state = await run_blog_pipeline(
        seed="agentic workloads on Azure landing zones",
        config=config,
        autonomous=True,
    )

    assert state.angle, "ideation should select an angle"
    assert "**" not in state.angle, "angle text should be markdown-free"
    assert state.internal_hits, "internal-knowledge stub should return Learn hits"
    assert all(
        "learn.microsoft.com" in c.url for c in state.internal_hits
    ), "internal hits must be Learn URLs"
    assert state.outline is not None, "planner should produce an outline"
    assert state.outline.sections, "stub planner should produce parseable sections"
    assert state.outline.pocs, "stub planner should include at least one PoC"
    assert state.poc_results, "PoC builder should produce at least one result"
    assert state.poc_results[0].exit_code == 0, "stub sandbox PoC should succeed"
    assert state.draft, "writer should produce a draft"
    assert state.diagram_excalidraw, "diagrammer should produce an excalidraw scene"
    assert state.diagram_mermaid, "diagrammer should produce a mermaid flowchart"
    assert "flowchart" in state.diagram_mermaid
    assert state.fact_findings is not None  # may be empty in stub mode, but the field exists
    assert state.critic_verdicts, "at least one critic verdict should be recorded"
    assert state.critic_verdicts[-1].total > 0, "critic verdict total should parse"
    assert state.critic_verdicts[-1].verdict == "accept"
    assert state.final_verdict == "approved"


def test_pipeline_sync_runner_via_asyncio() -> None:
    """The CLI uses asyncio.run; sanity-check that path works too."""
    config = AppConfig(
        stub=True,
        provider="stub",
        sandbox="stub",
        max_revisions=0,
        critic_threshold=0,
    )
    state = asyncio.run(
        run_blog_pipeline(
            seed="anything", config=config, autonomous=True
        )
    )
    assert state.draft is not None
