"""Per-agent model assignments.

Each agent role maps to a *deployment name* (what your Azure AI Foundry /
Azure OpenAI / OpenAI project actually calls the model). Defaults reflect the
plan, but every assignment can be overridden via
`BLOG_WRITER_MODEL_<ROLE>=<deployment-name>` env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Literal

AgentRole = Literal[
    "orchestrator",
    "ideation",
    "internal_knowledge",
    "research",
    "planner",
    "poc_builder",
    "writer",
    "fact_checker",
    "critic",
]

AGENT_ROLES: tuple[AgentRole, ...] = (
    "orchestrator",
    "ideation",
    "internal_knowledge",
    "research",
    "planner",
    "poc_builder",
    "writer",
    "fact_checker",
    "critic",
)


@dataclass(frozen=True)
class ModelMap:
    """Deployment name per agent role.

    Defaults to the latest Foundry-hosted models. Override via env vars or by
    passing a `ModelMap(...)` directly when constructing the pipeline (handy
    for tests).
    """

    orchestrator: str = "gpt-5.4"
    ideation: str = "gpt-5-mini"
    internal_knowledge: str = "gpt-5-mini"
    research: str = "gpt-5.4"
    planner: str = "o4-mini"
    poc_builder: str = "gpt-5.3-codex"
    writer: str = "claude-opus-4-7"
    fact_checker: str = "gpt-5-mini"
    critic: str = "gpt-5.4"

    def for_role(self, role: AgentRole) -> str:
        return getattr(self, role)


def load_model_map() -> ModelMap:
    """Build a ModelMap, honouring BLOG_WRITER_MODEL_<ROLE> env vars."""
    overrides: dict[str, str] = {}
    for f in fields(ModelMap):
        env_name = f"BLOG_WRITER_MODEL_{f.name.upper()}"
        value = os.environ.get(env_name)
        if value:
            overrides[f.name] = value
    return ModelMap(**overrides)
