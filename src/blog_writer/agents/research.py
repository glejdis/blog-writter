"""Research agent — Bing-grounded external fact-finding."""

from __future__ import annotations

from agent_framework import Agent

from blog_writer.agents.base import build_agent
from blog_writer.config import AppConfig
from blog_writer.models import ModelMap

# Bing grounding via Foundry is attached at the Foundry-agent layer (via a
# project connection). For local OpenAI providers and stub mode, the Research
# agent runs without a live web tool and relies on Internal Knowledge plus the
# bing_search_stub fallback invoked from the workflow.


def build_research_agent(config: AppConfig, models: ModelMap) -> Agent:
    return build_agent("research", config=config, models=models)
