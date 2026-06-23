"""Tests for revise_blog_post + the FastAPI UI server.

The pipeline tests run in stub mode (no model calls). The UI server tests
exercise the static / health endpoints and verify the WebSocket protocol's
basic shape using FastAPI's TestClient.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from blog_writer.config import load_config
from blog_writer.workflows import run_blog_pipeline
from blog_writer.workflows.blog_pipeline import revise_blog_post

# -----------------------------------------------------------------------------
# revise_blog_post (workflow-level)
# -----------------------------------------------------------------------------


async def test_revise_blog_post_requires_existing_draft() -> None:
    from blog_writer.workflows.state import BlogState

    state = BlogState(seed="x")
    config = load_config(stub=True)
    with pytest.raises(ValueError, match="no draft"):
        await revise_blog_post(state, "make it punchier", config=config)


async def test_revise_blog_post_rejects_empty_instruction() -> None:
    config = load_config(stub=True)
    state = await run_blog_pipeline(seed="agentic landing zones", config=config, autonomous=True)
    with pytest.raises(ValueError, match="empty"):
        await revise_blog_post(state, "   ", config=config)


async def test_revise_blog_post_emits_structured_events_and_bumps_iteration() -> None:
    config = load_config(stub=True)
    state = await run_blog_pipeline(seed="agentic landing zones", config=config, autonomous=True)
    original_iter = state.iteration
    assert state.draft is not None, "pipeline must produce a draft"

    events: list[dict[str, Any]] = []
    state = await revise_blog_post(
        state,
        "Add a section on cost guardrails.",
        config=config,
        on_event=events.append,
    )

    assert state.iteration == original_iter + 1
    assert state.draft is not None
    # In stub mode the writer regenerates a canned draft — confirm we at least
    # ran the stage and got the structured events the UI consumes.
    types = [e["type"] for e in events]
    assert "stage_start" in types
    assert "draft" in types
    assert "stage_end" in types
    assert any(e["type"] == "revision_done" for e in events)


async def test_revise_blog_post_can_skip_critic_and_fact_check() -> None:
    config = load_config(stub=True)
    state = await run_blog_pipeline(seed="agentic landing zones", config=config, autonomous=True)

    events: list[dict[str, Any]] = []
    state = await revise_blog_post(
        state,
        "shorter intro",
        config=config,
        on_event=events.append,
        run_fact_check=False,
        run_critic=False,
    )
    stages = {e.get("stage") for e in events if e["type"] == "stage_start"}
    assert "writer" in stages
    assert "fact_checker" not in stages
    assert "critic" not in stages


async def test_pipeline_emits_full_event_stream_in_stub_mode() -> None:
    """run_blog_pipeline with on_event should emit every UI-relevant event type."""
    events: list[dict[str, Any]] = []
    config = load_config(stub=True)
    await run_blog_pipeline(
        seed="agentic landing zones on azure",
        config=config,
        autonomous=True,
        on_event=events.append,
    )
    types = [e["type"] for e in events]

    # Required event types the SPA depends on.
    for required in ("stage_start", "stage_end", "log", "angles", "outline", "draft", "done"):
        assert required in types, f"missing event type: {required}"


async def test_pipeline_passes_toc_and_instructions_through_to_state() -> None:
    config = load_config(stub=True)
    state = await run_blog_pipeline(
        seed="agentic landing zones",
        config=config,
        autonomous=True,
        suggested_toc="- Intro\n- Networking\n- Identity",
        extra_instructions="Keep it under 800 words.",
    )
    assert "Networking" in state.suggested_toc
    assert "800 words" in state.extra_instructions


# -----------------------------------------------------------------------------
# UI server (HTTP + WebSocket smoke tests)
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> TestClient:
    from ui.server import app

    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_index_serves_html(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "<title>blog-writer" in body
    assert "/static/app.js" in body
    assert "/static/style.css" in body


def test_static_assets_served(client: TestClient) -> None:
    for path in ("/static/app.js", "/static/style.css", "/static/index.html"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} should be served"


def test_websocket_emits_ready_on_connect(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg == {"type": "ready"}


def test_websocket_rejects_bad_json(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # drain "ready"
        ws.send_text("{not json")
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_websocket_rejects_unknown_message_type(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "frobnicate"})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "frobnicate" in msg["message"]


def test_websocket_rejects_start_with_empty_topic(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "start", "topic": "   "})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "Topic" in msg["message"]


def test_websocket_full_stub_run(client: TestClient) -> None:
    """End-to-end stub pipeline via WebSocket — verifies the protocol works."""
    seen_types: list[str] = []
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json(
            {
                "type": "start",
                "topic": "agentic landing zones",
                "autonomous": True,
                "stub": True,
            }
        )
        # Drain until "persisted" (the last event after pipeline + persist).
        for _ in range(500):
            msg = ws.receive_json()
            seen_types.append(msg["type"])
            if msg["type"] == "persisted":
                break
        else:
            pytest.fail("pipeline did not emit 'persisted' within 500 messages")

    assert "draft" in seen_types
    assert "outline" in seen_types
    assert "done" in seen_types
    assert seen_types[-1] == "persisted"


def test_revise_via_websocket_after_pipeline(client: TestClient) -> None:
    """After a stub run, sending {type: 'revise'} should produce a new draft."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json(
            {"type": "start", "topic": "x", "autonomous": True, "stub": True}
        )
        for _ in range(500):
            msg = ws.receive_json()
            if msg["type"] == "persisted":
                break
        else:
            pytest.fail("pipeline did not finish")

        ws.send_json({"type": "revise", "instruction": "add a section on cost"})
        seen: list[str] = []
        for _ in range(200):
            msg = ws.receive_json()
            seen.append(msg["type"])
            if msg["type"] == "revision_persisted":
                break
            if msg["type"] == "error":
                pytest.fail(f"revise failed: {msg}")
        else:
            pytest.fail("revise did not complete")

        assert "draft" in seen
        assert "revision_done" in seen


def test_revise_without_pipeline_errors(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "revise", "instruction": "make it punchy"})
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_revise_with_empty_instruction_errors(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "revise", "instruction": "  "})
        msg = ws.receive_json()
        assert msg["type"] == "error"


# -----------------------------------------------------------------------------
# Improve mode (WebSocket)
# -----------------------------------------------------------------------------

_IMPROVE_DRAFT = """# Securing Agentic AI on Azure

## Identity before infrastructure

Managed identities replace secrets.

## Private networking

Use private endpoints for every dependency.
"""


def test_improve_without_draft_errors(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "improve", "draft": "   "})
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_improve_via_websocket_stub(client: TestClient) -> None:
    seen: list[str] = []
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "improve", "draft": _IMPROVE_DRAFT, "stub": True})
        for _ in range(500):
            msg = ws.receive_json()
            seen.append(msg["type"])
            if msg["type"] == "improve_persisted":
                break
            if msg["type"] == "error":
                pytest.fail(f"improve failed: {msg}")
        else:
            pytest.fail("improve did not emit 'improve_persisted'")

    assert "recommendations" in seen
    assert "draft" in seen
    assert "done" in seen
    assert seen[-1] == "improve_persisted"


def test_improve_recommend_only_via_websocket_stub(client: TestClient) -> None:
    persisted: dict[str, Any] = {}
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json(
            {
                "type": "improve",
                "draft": _IMPROVE_DRAFT,
                "stub": True,
                "recommend_only": True,
            }
        )
        for _ in range(500):
            msg = ws.receive_json()
            if msg["type"] == "improve_persisted":
                persisted = msg
                break
            if msg["type"] == "error":
                pytest.fail(f"improve failed: {msg}")
        else:
            pytest.fail("improve did not finish")

    # Recommend-only: a review + sources are saved, but no improved draft.
    assert persisted.get("improved_path") is None
    assert persisted.get("review_path")
    assert persisted.get("sources_path")
