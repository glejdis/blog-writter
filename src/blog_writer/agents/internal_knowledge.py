"""Internal Knowledge agent — queries the MS Learn Docs MCP server."""

from __future__ import annotations

from agent_framework import Agent

from blog_writer.agents.base import build_agent
from blog_writer.config import AppConfig
from blog_writer.models import ModelMap
from blog_writer.tools.learn_mcp import build_learn_mcp_tool


def build_internal_knowledge_agent(config: AppConfig, models: ModelMap) -> Agent:
    # In stub mode we don't attach the MCP tool — the StubChatClient ignores
    # tools anyway, and skipping the construction avoids needing network at
    # import time in tests.
    tools = None if config.stub else [build_learn_mcp_tool()]
    return build_agent("internal_knowledge", config=config, models=models, tools=tools)
