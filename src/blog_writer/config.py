"""Application-wide configuration.

Reads from environment (or a `.env` file) and exposes a single immutable
`AppConfig` instance. Pulled from one place so the rest of the codebase doesn't
sprinkle os.getenv calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

Provider = Literal["foundry", "azure_openai", "openai", "stub"]
Sandbox = Literal["local", "aca", "stub"]


class AppConfig(BaseSettings):
    """Top-level application config, populated from env vars / .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="BLOG_WRITER_",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Mode ----------------------------------------------------------------
    stub: bool = Field(default=False, description="Run end-to-end with stub agents and tools.")
    provider: Provider = Field(default="foundry", description="Which chat-client backend to use.")

    # ---- Workflow ------------------------------------------------------------
    max_revisions: int = Field(default=3, ge=0, le=10)
    critic_threshold: int = Field(default=80, ge=0, le=100)
    max_learn_hits: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Max number of in-scope MS Learn citations to keep per post.",
    )
    max_poc_attempts: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Max attempts the PoC Builder gets to produce a passing sample.",
    )
    diagrams: bool = Field(
        default=True,
        description=(
            "Generate an Excalidraw architecture diagram (and an embeddable Mermaid "
            "flowchart) for each post. Disable with BLOG_WRITER_DIAGRAMS=false."
        ),
    )
    style: bool = Field(
        default=True,
        description=(
            "Run the Stylist agent: learn a house writing style from the example "
            "posts in knowledge_base/style_corpus/ and have the Writer follow it. "
            "No-op when the corpus is empty. Disable with BLOG_WRITER_STYLE=false."
        ),
    )

    # ---- Tools ---------------------------------------------------------------
    sandbox: Sandbox = "local"
    ms_learn_mcp_url: str = Field(
        default="https://learn.microsoft.com/api/mcp",
        description="Override the MS Learn Docs MCP endpoint (rarely needed).",
    )
    deep_research: bool = Field(
        default=True,
        description=(
            "Use the Foundry o3-deep-research model (agentic, Bing-grounded) for the "
            "external research stage instead of the lightweight Learn/GitHub search. "
            "On by default; automatically falls back to lightweight search when the "
            "AZURE_AI_DEEP_RESEARCH_* env vars + Bing grounding connection are absent. "
            "Disable with BLOG_WRITER_DEEP_RESEARCH=false."
        ),
    )

    # ---- Paths ---------------------------------------------------------------
    drafts_dir: Path = PROJECT_ROOT / "drafts"
    samples_dir: Path = PROJECT_ROOT / "samples"
    knowledge_base_dir: Path = PROJECT_ROOT / "knowledge_base"
    style_corpus_dir: Path = PROJECT_ROOT / "knowledge_base" / "style_corpus"

    def ensure_dirs(self) -> None:
        for d in (self.drafts_dir, self.samples_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_config(**overrides: object) -> AppConfig:
    """Load config from env + .env, with optional overrides (used by tests / CLI flags)."""
    return AppConfig(**overrides)  # type: ignore[arg-type]
