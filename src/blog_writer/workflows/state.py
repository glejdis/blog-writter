"""Shared workflow state — the dataclasses passed between executors.

A single `BlogState` is threaded through every stage of the pipeline; each
agent reads what it needs and appends its contribution. Keeping state in one
place makes the pipeline easy to checkpoint, log, and unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Citation:
    """One source the post can cite. `kind` controls citation priority."""

    key: str
    kind: Literal["learn", "external"]
    title: str
    url: str
    summary: str


@dataclass
class Section:
    """One H2 section of the outline."""

    heading: str
    argues: str
    leans_on: list[str] = field(default_factory=list)


@dataclass
class PoCSpec:
    """A PoC the Planner wants the PoC Builder to generate."""

    id: str
    section: str
    description: str
    language: str = "python"
    sandbox: Literal["local", "none"] = "local"


@dataclass
class PoCResult:
    """The outcome of generating + executing a single PoC."""

    spec: PoCSpec
    code: str
    code_path: str
    exit_code: int
    stdout: str
    stderr: str
    attempts: int
    narrative: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class Outline:
    title: str
    summary: str
    sections: list[Section] = field(default_factory=list)
    pocs: list[PoCSpec] = field(default_factory=list)


@dataclass
class FactCheckFinding:
    section: str
    claim: str
    status: Literal["supported", "partial", "unsupported", "missing-citation"]
    citation: str | None
    suggestion: str | None


@dataclass
class CriticVerdict:
    total: int
    verdict: Literal["accept", "revise"]
    feedback: list[str] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)


@dataclass
class BlogState:
    """Everything the pipeline accumulates while building a single post."""

    # Inputs
    seed: str

    # Optional user-provided steering from the UI
    extra_instructions: str = ""
    suggested_toc: str = ""

    # After ideation + human pick
    angles: list[str] = field(default_factory=list)
    angle: str | None = None

    # After research
    internal_hits: list[Citation] = field(default_factory=list)
    external_hits: list[Citation] = field(default_factory=list)
    # Synthesized narrative from the deep-research model (empty unless enabled).
    research_report: str = ""

    # After planning
    outline: Outline | None = None
    plan_approved: bool = False

    # After PoCs
    poc_results: list[PoCResult] = field(default_factory=list)

    # After diagramming (architecture view)
    diagram_title: str = ""
    diagram_excalidraw: str = ""  # self-contained .excalidraw JSON
    diagram_mermaid: str = ""  # embeddable flowchart for the draft

    # After writing
    draft: str | None = None
    iteration: int = 0

    # After fact-check
    fact_findings: list[FactCheckFinding] = field(default_factory=list)

    # After critic
    critic_verdicts: list[CriticVerdict] = field(default_factory=list)

    # After final orchestrator review
    final_verdict: Literal["approved", "revise"] | None = None
    final_notes: str | None = None

    @property
    def all_citations(self) -> list[Citation]:
        """Citations in priority order: Learn first, then external."""
        return list(self.internal_hits) + list(self.external_hits)

    @property
    def latest_critic(self) -> CriticVerdict | None:
        return self.critic_verdicts[-1] if self.critic_verdicts else None
