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

    # ---- Tools ---------------------------------------------------------------
    sandbox: Sandbox = "local"

    # ---- Paths ---------------------------------------------------------------
    drafts_dir: Path = PROJECT_ROOT / "drafts"
    samples_dir: Path = PROJECT_ROOT / "samples"
    knowledge_base_dir: Path = PROJECT_ROOT / "knowledge_base"

    def ensure_dirs(self) -> None:
        for d in (self.drafts_dir, self.samples_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_config(**overrides: object) -> AppConfig:
    """Load config from env + .env, with optional overrides (used by tests / CLI flags)."""
    return AppConfig(**overrides)  # type: ignore[arg-type]
