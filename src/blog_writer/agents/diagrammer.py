"""Diagrammer agent — emits a JSON architecture spec we render to Excalidraw."""

from __future__ import annotations

from agent_framework import Agent

from blog_writer.agents.base import build_agent
from blog_writer.config import AppConfig
from blog_writer.models import ModelMap


def build_diagrammer_agent(config: AppConfig, models: ModelMap) -> Agent:
    return build_agent("diagrammer", config=config, models=models)
