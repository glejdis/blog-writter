"""Stylist agent — learns a house writing style from an example corpus."""

from __future__ import annotations

from agent_framework import Agent

from blog_writer.agents.base import build_agent
from blog_writer.config import AppConfig
from blog_writer.models import ModelMap


def build_stylist_agent(config: AppConfig, models: ModelMap) -> Agent:
    return build_agent("stylist", config=config, models=models)
