"""Tests for the Stylist agent — corpus loading + pipeline integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from blog_writer.config import AppConfig
from blog_writer.models import get_chat_client, load_model_map
from blog_writer.models.config import AGENT_ROLES
from blog_writer.models.providers import StubChatClient
from blog_writer.tools.style_corpus import load_style_corpus, style_corpus_files
from blog_writer.workflows import run_blog_pipeline
from blog_writer.workflows.blog_pipeline import _style_block, _writer_inputs
from blog_writer.workflows.state import BlogState


def test_stylist_is_a_known_role() -> None:
    assert "stylist" in AGENT_ROLES
    assert load_model_map().for_role("stylist")  # type: ignore[arg-type]


def test_stylist_uses_stub_client_in_stub_mode() -> None:
    config = AppConfig(stub=True, provider="stub")
    client = get_chat_client("stylist", config=config, models=load_model_map())
    assert isinstance(client, StubChatClient)


def test_load_style_corpus_reads_and_orders_files(tmp_path: Path) -> None:
    corpus = tmp_path / "style_corpus"
    corpus.mkdir()
    (corpus / "README.md").write_text("ignore me", encoding="utf-8")
    (corpus / "b-post.md").write_text("Beta example.", encoding="utf-8")
    (corpus / "_ref.md").write_text("Shared reference.", encoding="utf-8")

    files = style_corpus_files(corpus)
    # README is excluded; underscore-prefixed reference sorts first.
    assert [p.name for p in files] == ["_ref.md", "b-post.md"]

    text = load_style_corpus(corpus)
    assert "Shared reference." in text
    assert "Beta example." in text
    assert "ignore me" not in text
    assert text.index("_ref.md") < text.index("b-post.md")


def test_load_style_corpus_empty_when_missing(tmp_path: Path) -> None:
    assert load_style_corpus(tmp_path / "does-not-exist") == ""


def test_style_block_injected_into_writer_inputs() -> None:
    state = BlogState(seed="x")
    assert _style_block(state) == ""  # empty by default
    state.style_guide = "# House Style Card\n- Voice: dry."
    block = _style_block(state)
    assert "House Style Card" in block
    assert "House style to follow" in block
    assert "House Style Card" in _writer_inputs(state)


@pytest.mark.asyncio
async def test_pipeline_populates_style_guide_in_stub_mode(tmp_path: Path) -> None:
    config = AppConfig(
        stub=True,
        provider="stub",
        sandbox="stub",
        max_revisions=0,
        critic_threshold=0,
        drafts_dir=tmp_path / "drafts",
        samples_dir=tmp_path / "samples",
    )
    state = await run_blog_pipeline(seed="anything", config=config, autonomous=True)
    # The shipped corpus is non-empty, so the Stylist stage should run and the
    # stub client returns a canned Style Card.
    assert state.style_guide, "stylist stage should populate the style guide"
    assert "Style Card" in state.style_guide


@pytest.mark.asyncio
async def test_style_stage_skipped_when_disabled(tmp_path: Path) -> None:
    config = AppConfig(
        stub=True,
        provider="stub",
        sandbox="stub",
        style=False,
        max_revisions=0,
        critic_threshold=0,
        drafts_dir=tmp_path / "drafts",
        samples_dir=tmp_path / "samples",
    )
    state = await run_blog_pipeline(seed="anything", config=config, autonomous=True)
    assert state.style_guide == "", "style guide should be empty when stage disabled"
    assert state.draft, "writer should still produce a draft"
