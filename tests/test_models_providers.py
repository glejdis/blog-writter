"""Tests for the per-agent model assignment + provider factory."""

from __future__ import annotations

from blog_writer.config import AppConfig
from blog_writer.models import get_chat_client, load_model_map
from blog_writer.models.providers import StubChatClient


def test_default_model_map_assigns_every_role() -> None:
    m = load_model_map()
    for role in (
        "orchestrator",
        "ideation",
        "internal_knowledge",
        "research",
        "planner",
        "poc_builder",
        "writer",
        "fact_checker",
        "critic",
    ):
        assert m.for_role(role)  # type: ignore[arg-type]


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("BLOG_WRITER_MODEL_WRITER", "custom-writer-model")
    m = load_model_map()
    assert m.for_role("writer") == "custom-writer-model"


def test_stub_client_used_in_stub_mode() -> None:
    config = AppConfig(stub=True, provider="stub")
    m = load_model_map()
    client = get_chat_client("ideation", config=config, models=m)
    assert isinstance(client, StubChatClient)


def test_stub_client_falls_back_when_no_creds(monkeypatch) -> None:
    for k in ("OPENAI_API_KEY", "AZURE_AI_PROJECT_ENDPOINT", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.delenv(k, raising=False)
    config = AppConfig(stub=False, provider="openai")
    m = load_model_map()
    client = get_chat_client("ideation", config=config, models=m)
    # Without an API key, the OpenAI factory falls back to a stub.
    assert isinstance(client, StubChatClient)
