"""Tests for filesystem helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from blog_writer.tools.fs import safe_write, slugify


def test_slugify_basic() -> None:
    assert slugify("Hello World") == "hello-world"
    assert slugify("AI Foundry: Agent Service!") == "ai-foundry-agent-service"


def test_slugify_handles_empty_and_punctuation() -> None:
    assert slugify("") == "post"
    assert slugify("!!! ???") == "post"


def test_slugify_max_len() -> None:
    long = "a" * 100
    assert len(slugify(long)) <= 60


def test_safe_write_writes_under_root(tmp_path: Path) -> None:
    p = safe_write(tmp_path, "sub/dir/file.md", "hi")
    assert p.exists()
    assert p.read_text() == "hi"
    assert tmp_path in p.parents


def test_safe_write_refuses_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_write(tmp_path, "../escape.md", "no")
