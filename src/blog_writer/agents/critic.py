"""Critic agent — scores the draft against a rubric, loops with writer."""

from __future__ import annotations

from agent_framework import Agent

from blog_writer.agents.base import build_agent
from blog_writer.config import AppConfig
from blog_writer.models import ModelMap


def build_critic_agent(config: AppConfig, models: ModelMap) -> Agent:
    return build_agent("critic", config=config, models=models)
