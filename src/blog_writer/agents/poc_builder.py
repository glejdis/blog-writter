"""PoC Builder agent — generates code, runs it in a sandbox, captures output.

Attaches the MS Learn ``microsoft_code_sample_search`` tool so the agent can
search official Microsoft Learn for an existing snippet before writing one
from scratch.
"""

from __future__ import annotations

from agent_framework import Agent

from blog_writer.agents.base import build_agent
from blog_writer.config import AppConfig
from blog_writer.models import ModelMap
from blog_writer.tools.learn_mcp import build_learn_code_sample_tool


def build_poc_builder_agent(config: AppConfig, models: ModelMap) -> Agent:
    tools = None if config.stub else [build_learn_code_sample_tool()]
    return build_agent("poc_builder", config=config, models=models, tools=tools)
