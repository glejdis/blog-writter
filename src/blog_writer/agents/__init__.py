"""Agent factories — one builder per role.

Each module exposes a `build_*_agent(config, models, **role_tools)` function
that returns a fully-configured `agent_framework.Agent` ready to call.

The actual orchestration (who calls whom, in what order) lives in
`blog_writer/workflows/blog_pipeline.py`.
"""

from blog_writer.agents.base import build_agent
from blog_writer.agents.critic import build_critic_agent
from blog_writer.agents.fact_checker import build_fact_checker_agent
from blog_writer.agents.ideation import build_ideation_agent
from blog_writer.agents.internal_knowledge import build_internal_knowledge_agent
from blog_writer.agents.orchestrator import build_orchestrator_agent
from blog_writer.agents.planner import build_planner_agent
from blog_writer.agents.poc_builder import build_poc_builder_agent
from blog_writer.agents.research import build_research_agent
from blog_writer.agents.writer import build_writer_agent

__all__ = [
    "build_agent",
    "build_critic_agent",
    "build_fact_checker_agent",
    "build_ideation_agent",
    "build_internal_knowledge_agent",
    "build_orchestrator_agent",
    "build_planner_agent",
    "build_poc_builder_agent",
    "build_research_agent",
    "build_writer_agent",
]
