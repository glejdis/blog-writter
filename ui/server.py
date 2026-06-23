"""FastAPI + WebSocket server for the blog-writer chat UI.

Wire protocol — JSON messages on the WebSocket
================================================

**Client → server**

* ``{type: "start", topic, toc?, instructions?, autonomous?, stub?, reference_draft?}``
* ``{type: "improve", draft?, path?, topic?, deep_research?, recommend_only?, stub?}``
* ``{type: "answer", value}``      — user picked an option / typed a free-form answer
* ``{type: "revise", instruction}``— after the first draft is ready, ask for changes
* ``{type: "done"}``               — accept the current draft as final

**Server → client**

* ``{type: "ready"}``                                      — connected, awaiting "start"
* ``{type: "stage_start", stage, label}`` / ``stage_end``  — agent boundary markers
* ``{type: "log", message}``                               — line-of-text progress
* ``{type: "angles", angles: [...]}``                      — angle picker payload
* ``{type: "outline", title, sections, pocs}``             — plan summary
* ``{type: "poc_result", id, exit_code, attempts}``        — per-PoC status
* ``{type: "critic", round, total, verdict, feedback}``    — critic round result
* ``{type: "fact_findings", items}``                       — fact-checker output
* ``{type: "draft", markdown, iteration}``                 — current draft body
* ``{type: "citations", kind, items}``                     — internal/external sources
* ``{type: "recommendations", items, total}``              — improvement recommendations
* ``{type: "ask", id, prompt, choices?}``                  — please answer
* ``{type: "revision_done", iteration}``                   — after a revise turn
* ``{type: "revision_persisted", draft_path, sources_path}`` — after revision is saved
* ``{type: "improve_persisted", improved_path, review_path, sources_path}`` — after improve is saved
* ``{type: "done", final_verdict}``                        — pipeline emitted by the workflow itself
* ``{type: "persisted", draft_path, sources_path}``        — server saved the draft to disk
* ``{type: "error", message}``                             — something blew up
* ``{type: "pong"}``                                       — heartbeat reply
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from blog_writer.config import PROJECT_ROOT, load_config

# Load .env into os.environ so vars read via os.environ.get (e.g. the provider
# endpoint and deep-research settings in models/providers.py and
# tools/deep_research.py) are populated when the UI is launched directly via
# `python -m ui.server` (the CLI launcher loads it too, but this makes the
# server self-sufficient).
load_dotenv(PROJECT_ROOT / ".env")
from blog_writer.observability import setup_observability
from blog_writer.workflows.blog_pipeline import (
    build_review_report,
    improve_blog_post,
    revise_blog_post,
    run_blog_pipeline,
)
from blog_writer.workflows.state import BlogState

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


app = FastAPI(title="blog-writer chat UI", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


# -----------------------------------------------------------------------------
# Per-WebSocket session
# -----------------------------------------------------------------------------


@dataclass
class WSSession:
    """Per-connection state. Each WebSocket gets a fresh one."""

    ws: WebSocket
    out_queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    pending_answer: asyncio.Future[str] | None = None
    state: BlogState | None = None
    pipeline_task: asyncio.Task[BlogState] | None = None
    _ask_counter: int = 0

    def emit(self, event: dict[str, Any]) -> None:
        """Push an event onto the out-queue (drains in the writer task)."""
        self.out_queue.put_nowait(event)

    async def ask(self, prompt: str, choices: list[str] | None = None) -> str:
        """Surface a HITL question to the client and await the answer."""
        self._ask_counter += 1
        ask_id = self._ask_counter
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self.pending_answer = fut
        self.emit({"type": "ask", "id": ask_id, "prompt": prompt, "choices": choices})
        try:
            return await fut
        finally:
            self.pending_answer = None


async def _writer_loop(session: WSSession) -> None:
    """Drain the out-queue and ship events to the client."""
    while True:
        event = await session.out_queue.get()
        try:
            await session.ws.send_json(event)
        except (RuntimeError, WebSocketDisconnect):
            return


def _slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9\s-]", "", value).strip().lower()
    return re.sub(r"[\s-]+", "-", value)[:80] or "post"


async def _persist_draft(state: BlogState) -> tuple[str | None, str | None]:
    """Write the final draft + sources.json to ``drafts_dir`` and return paths."""
    config = load_config()
    config.ensure_dirs()
    if not state.draft:
        return None, None
    slug = _slugify(state.angle or state.seed)
    draft_path = Path(config.drafts_dir) / f"{slug}.md"
    sources_path = Path(config.drafts_dir) / f"{slug}.sources.json"
    draft_path.write_text(state.draft, encoding="utf-8")
    diagram_path: str | None = None
    if state.diagram_excalidraw:
        excalidraw_path = Path(config.drafts_dir) / f"{slug}.excalidraw"
        excalidraw_path.write_text(state.diagram_excalidraw, encoding="utf-8")
        diagram_path = str(excalidraw_path)
    sources_payload = {
        "seed": state.seed,
        "angle": state.angle,
        "internal": [c.__dict__ for c in state.internal_hits],
        "external": [c.__dict__ for c in state.external_hits],
        "outline": (
            {
                "title": state.outline.title,
                "sections": [s.__dict__ for s in state.outline.sections],
                "pocs": [p.__dict__ for p in state.outline.pocs],
            }
            if state.outline
            else None
        ),
        "diagram": (
            {"title": state.diagram_title, "path": diagram_path}
            if diagram_path
            else None
        ),
        "final_verdict": state.final_verdict,
    }
    sources_path.write_text(json.dumps(sources_payload, indent=2), encoding="utf-8")
    return str(draft_path), str(sources_path)


async def _run_pipeline_in_session(
    session: WSSession,
    *,
    topic: str,
    toc: str | None,
    instructions: str | None,
    autonomous: bool,
    stub: bool,
    reference_draft: str | None = None,
) -> None:
    """Drive the pipeline for one user 'start' request."""
    config = load_config(stub=stub)
    config.ensure_dirs()
    setup_observability(config)

    def progress(msg: str) -> None:
        # also surfaced as 'log' events by the pipeline; this stays for stdout debugging.
        logger.info("[pipeline] %s", msg)

    def on_event(event: dict[str, Any]) -> None:
        session.emit(event)

    async def on_human_input(prompt: str, choices: list[str] | None) -> str:
        return await session.ask(prompt, choices)

    try:
        state = await run_blog_pipeline(
            seed=topic,
            config=config,
            on_human_input=None if autonomous else on_human_input,
            autonomous=autonomous,
            progress=progress,
            on_event=on_event,
            extra_instructions=instructions,
            suggested_toc=toc,
            reference_draft=reference_draft,
        )
        session.state = state
        draft_path, sources_path = await _persist_draft(state)
        # The pipeline already emitted its own "done"; we add a "persisted"
        # event so the SPA can show where the draft was written.
        session.emit(
            {
                "type": "persisted",
                "draft_path": draft_path,
                "sources_path": sources_path,
            }
        )
    except asyncio.CancelledError:
        session.emit({"type": "log", "message": "Pipeline cancelled."})
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline failed")
        session.emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})


async def _run_revision_in_session(session: WSSession, instruction: str) -> None:
    """Apply a single revision pass to the current draft."""
    if session.state is None or not session.state.draft:
        session.emit(
            {"type": "error", "message": "Nothing to revise yet — run the pipeline first."}
        )
        return
    config = load_config()

    def on_event(event: dict[str, Any]) -> None:
        session.emit(event)

    try:
        session.state = await revise_blog_post(
            session.state,
            instruction,
            config=config,
            on_event=on_event,
        )
        draft_path, sources_path = await _persist_draft(session.state)
        session.emit(
            {
                "type": "revision_persisted",
                "draft_path": draft_path,
                "sources_path": sources_path,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Revision failed")
        session.emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})


async def _persist_improve(
    state: BlogState, *, recommend_only: bool
) -> tuple[str | None, str | None, str | None]:
    """Write the review report, sources.json, and (optionally) the improved draft."""
    config = load_config()
    config.ensure_dirs()
    drafts = Path(config.drafts_dir)
    slug = _slugify(state.angle or state.seed)

    review_path = drafts / f"{slug}.review.md"
    review_path.write_text(build_review_report(state), encoding="utf-8")

    crit = state.latest_critic
    sources_path = drafts / f"{slug}.sources.json"
    sources_path.write_text(
        json.dumps(
            {
                "title": state.angle,
                "critic_total": crit.total if crit else None,
                "recommendations": list(crit.feedback) if crit else [],
                "citations": [c.__dict__ for c in state.all_citations],
                "fact_findings": [
                    {
                        "section": f.section,
                        "status": f.status,
                        "claim": f.claim,
                        "suggestion": f.suggestion,
                    }
                    for f in state.fact_findings
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    improved_path: str | None = None
    if not recommend_only and state.draft:
        path = drafts / f"{slug}.improved.md"
        path.write_text(state.draft, encoding="utf-8")
        improved_path = str(path)
    return improved_path, str(review_path), str(sources_path)


async def _run_improve_in_session(
    session: WSSession,
    *,
    draft_text: str,
    topic: str | None,
    deep_research: bool,
    recommend_only: bool,
    stub: bool,
) -> None:
    """Improve an existing draft: find sources, recommend changes, re-cite."""
    config = load_config(stub=stub, deep_research=deep_research)
    config.ensure_dirs()
    setup_observability(config)

    def on_event(event: dict[str, Any]) -> None:
        session.emit(event)

    try:
        state = await improve_blog_post(
            draft_text,
            config=config,
            topic=topic,
            on_event=on_event,
            rewrite=not recommend_only,
        )
        session.state = state
        improved_path, review_path, sources_path = await _persist_improve(
            state, recommend_only=recommend_only
        )
        session.emit(
            {
                "type": "improve_persisted",
                "improved_path": improved_path,
                "review_path": review_path,
                "sources_path": sources_path,
            }
        )
    except asyncio.CancelledError:
        session.emit({"type": "log", "message": "Improve cancelled."})
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Improve failed")
        session.emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})


# -----------------------------------------------------------------------------
# WebSocket endpoint
# -----------------------------------------------------------------------------


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session = WSSession(ws=websocket)
    writer_task = asyncio.create_task(_writer_loop(session))
    session.emit({"type": "ready"})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                session.emit({"type": "error", "message": "Invalid JSON from client."})
                continue

            mtype = msg.get("type")

            if mtype == "start":
                if session.pipeline_task and not session.pipeline_task.done():
                    session.emit(
                        {"type": "error", "message": "A pipeline is already running."}
                    )
                    continue
                topic = (msg.get("topic") or "").strip()
                if not topic:
                    session.emit({"type": "error", "message": "Topic is required."})
                    continue
                session.pipeline_task = asyncio.create_task(
                    _run_pipeline_in_session(
                        session,
                        topic=topic,
                        toc=(msg.get("toc") or None),
                        instructions=(msg.get("instructions") or None),
                        autonomous=bool(msg.get("autonomous")),
                        stub=bool(msg.get("stub")),
                        reference_draft=(msg.get("reference_draft") or None),
                    )
                )

            elif mtype == "improve":
                if session.pipeline_task and not session.pipeline_task.done():
                    session.emit(
                        {"type": "error", "message": "A run is already in progress."}
                    )
                    continue
                draft_text = msg.get("draft") or ""
                path = (msg.get("path") or "").strip()
                if path:
                    file = Path(path)
                    if not file.exists():
                        session.emit({"type": "error", "message": f"File not found: {path}"})
                        continue
                    try:
                        draft_text = file.read_text(encoding="utf-8")
                    except OSError as exc:
                        session.emit({"type": "error", "message": f"Could not read file: {exc}"})
                        continue
                if not draft_text.strip():
                    session.emit(
                        {
                            "type": "error",
                            "message": "Provide a draft — paste Markdown or give a server file path.",
                        }
                    )
                    continue
                session.pipeline_task = asyncio.create_task(
                    _run_improve_in_session(
                        session,
                        draft_text=draft_text,
                        topic=((msg.get("topic") or "").strip() or None),
                        deep_research=bool(msg.get("deep_research")),
                        recommend_only=bool(msg.get("recommend_only")),
                        stub=bool(msg.get("stub")),
                    )
                )

            elif mtype == "answer":
                value = str(msg.get("value", ""))
                if session.pending_answer and not session.pending_answer.done():
                    session.pending_answer.set_result(value)
                else:
                    session.emit(
                        {"type": "error", "message": "No pending question to answer."}
                    )

            elif mtype == "revise":
                instruction = (msg.get("instruction") or "").strip()
                if not instruction:
                    session.emit(
                        {"type": "error", "message": "Revision instruction is empty."}
                    )
                    continue
                if session.pipeline_task and not session.pipeline_task.done():
                    session.emit(
                        {"type": "error", "message": "Wait for the pipeline to finish first."}
                    )
                    continue
                # fire-and-forget; events stream back via the writer loop
                asyncio.create_task(_run_revision_in_session(session, instruction))

            elif mtype == "done":
                session.emit(
                    {"type": "log", "message": "Draft accepted. You can close this tab."}
                )

            elif mtype == "ping":
                session.emit({"type": "pong"})

            else:
                session.emit(
                    {"type": "error", "message": f"Unknown message type: {mtype!r}"}
                )

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    finally:
        if session.pipeline_task and not session.pipeline_task.done():
            session.pipeline_task.cancel()
        writer_task.cancel()


# -----------------------------------------------------------------------------
# CLI launcher (also wired into blog-writer ui)
# -----------------------------------------------------------------------------


def run(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
    log_level: str = "info",
) -> None:
    """Start uvicorn pointing at this app. Used by the CLI launcher."""
    import uvicorn

    uvicorn.run(
        "ui.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m ui.server")
    parser.add_argument("--host", default=os.environ.get("BLOG_WRITER_UI_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("BLOG_WRITER_UI_PORT", "8000"))
    )
    parser.add_argument("--reload", action="store_true", help="Enable hot reload.")
    parser.add_argument(
        "--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"]
    )
    args = parser.parse_args(argv)
    run(host=args.host, port=args.port, reload=args.reload, log_level=args.log_level)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
