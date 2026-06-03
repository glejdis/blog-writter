"""Shared agent builder."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_framework import Agent

from blog_writer.config import AppConfig
from blog_writer.models import ModelMap, get_chat_client
from blog_writer.models.config import AgentRole
from blog_writer.prompts import load_prompt


def build_agent(
    role: AgentRole,
    *,
    config: AppConfig,
    models: ModelMap,
    tools: Sequence[Any] | None = None,
    name_suffix: str = "",
) -> Agent[Any]:
    """Build a standard `Agent` for `role` — chat client + prompt + tools."""
    client = get_chat_client(role, config=config, models=models)
    instructions = load_prompt(role)
    return Agent(
        client,
        instructions,
        name=f"{role}{name_suffix}",
        description=f"{role.replace('_', ' ').title()} agent for the blog-writer pipeline.",
        tools=list(tools) if tools else None,
    )
