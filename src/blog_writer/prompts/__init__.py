"""Prompt loader.

System prompts are stored as Markdown files alongside this module so they're
easy to edit, version, and review in pull requests independently of code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from blog_writer.models.config import AgentRole

PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(role: AgentRole) -> str:
    """Read the system prompt for `role` from `prompts/<role>.md`."""
    path = PROMPTS_DIR / f"{role}.md"
    if not path.exists():
        raise FileNotFoundError(f"No prompt file for role {role!r} at {path}")
    return path.read_text(encoding="utf-8").strip()


__all__ = ["PROMPTS_DIR", "load_prompt"]
