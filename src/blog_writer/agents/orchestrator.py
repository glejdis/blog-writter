"""Orchestrator (editor-in-chief) — final review of the assembled package."""

from __future__ import annotations

from agent_framework import Agent

from blog_writer.agents.base import build_agent
from blog_writer.config import AppConfig
from blog_writer.models import ModelMap


def build_orchestrator_agent(config: AppConfig, models: ModelMap) -> Agent:
    return build_agent("orchestrator", config=config, models=models)
