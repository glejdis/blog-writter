"""Planner agent — outline + PoC spec."""

from __future__ import annotations

from agent_framework import Agent

from blog_writer.agents.base import build_agent
from blog_writer.config import AppConfig
from blog_writer.models import ModelMap


def build_planner_agent(config: AppConfig, models: ModelMap) -> Agent:
    return build_agent("planner", config=config, models=models)
