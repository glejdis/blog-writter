"""`blog-writer` CLI.

Single command:

    blog-writer new --seed "agentic Azure topics" [--autonomous] [--stub]

Writes:

    drafts/<slug>.md
    samples/<slug>/<files...>
    drafts/<slug>.sources.json

Uses rich for nice console output and a small async wrapper for the human
checkpoint prompts.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt

# Make sure UTF-8 output works on legacy Windows consoles (cp1252 chokes on
# some symbols we emit). Best-effort — falls back silently on platforms that
# don't expose `reconfigure`.
for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

from dotenv import load_dotenv

from blog_writer.config import PROJECT_ROOT, AppConfig, load_config
from blog_writer.observability import setup_observability
from blog_writer.tools.fs import safe_write, slugify
from blog_writer.workflows import BlogState, run_blog_pipeline

# Load .env into os.environ so vars read via os.environ.get (e.g.
# AZURE_OPENAI_ENDPOINT) are available, not just the BLOG_WRITER_-prefixed
# settings consumed by pydantic-settings.
load_dotenv(PROJECT_ROOT / ".env")

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


@app.callback()
def _root_callback() -> None:
    """blog-writer — multi-agent system for Azure infra & agentic AI blog posts."""
    # An explicit (no-op) callback keeps typer in multi-command mode so
    # `blog-writer new ...` works even when there's only one subcommand.
    return


@app.command("new")
def new_post(
    seed: Annotated[str, typer.Option("--seed", "-s", help="Seed topic for the post.")],
    autonomous: Annotated[
        bool,
        typer.Option("--autonomous", help="Skip both human checkpoints (angle + plan)."),
    ] = False,
    stub: Annotated[
        bool,
        typer.Option("--stub", help="Run end-to-end with stub agents and tools (no model calls)."),
    ] = False,
    max_revisions: Annotated[
        int | None,
        typer.Option("--max-revisions", help="Override max writer/critic revision rounds."),
    ] = None,
    threshold: Annotated[
        int | None,
        typer.Option("--threshold", help="Override critic score threshold (0-100)."),
    ] = None,
) -> None:
    """Generate a new blog post from a seed topic."""
    overrides: dict[str, object] = {"stub": stub}
    if max_revisions is not None:
        overrides["max_revisions"] = max_revisions
    if threshold is not None:
        overrides["critic_threshold"] = threshold
    config = load_config(**overrides)
    config.ensure_dirs()

    # Wire OpenTelemetry exporters once per process. No-op if no telemetry
    # backend is configured (env vars not set).
    setup_observability(config)

    state = asyncio.run(
        run_blog_pipeline(
            seed=seed,
            config=config,
            autonomous=autonomous,
            on_human_input=None if autonomous else _human_input,
            progress=_progress,
        )
    )
    _persist_outputs(state, config)
    _print_summary(state)


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------


def _progress(message: str) -> None:
    console.log(f"[blue]·[/blue] {message}")


async def _human_input(prompt: str, choices: list[str] | None) -> str:
    """Render the prompt and wait for the user's answer.

    Runs in a thread so the surrounding async pipeline isn't blocked.
    """

    def _ask() -> str:
        if choices:
            console.print(Panel(prompt, title="Human checkpoint", border_style="cyan"))
            for i, c in enumerate(choices, start=1):
                console.print(f"  [bold]{i}[/bold]. {c}")
            idx = IntPrompt.ask("Pick #", default=1)
            idx = max(1, min(idx, len(choices)))
            return choices[idx - 1]
        return Prompt.ask(prompt)

    return await asyncio.to_thread(_ask)


def _persist_outputs(state: BlogState, config: AppConfig) -> None:
    slug = slugify(
        (state.outline.title if state.outline else state.angle or state.seed) or state.seed
    )
    draft_path = safe_write(
        config.drafts_dir,
        f"{slug}.md",
        state.draft or "# (empty draft)\n",
    )
    sources_path = safe_write(
        config.drafts_dir,
        f"{slug}.sources.json",
        json.dumps(
            {
                "seed": state.seed,
                "angle": state.angle,
                "final_verdict": state.final_verdict,
                "iterations": state.iteration,
                "citations": [c.__dict__ for c in state.all_citations],
                "pocs": [
                    {
                        "id": r.spec.id,
                        "path": r.code_path,
                        "exit_code": r.exit_code,
                        "stdout_excerpt": r.stdout[:200],
                    }
                    for r in state.poc_results
                ],
                "critic_history": [
                    {"total": v.total, "verdict": v.verdict, "feedback": v.feedback}
                    for v in state.critic_verdicts
                ],
            },
            indent=2,
        ),
    )
    for result in state.poc_results:
        ext = "py" if result.spec.language in {"python", "py"} else result.spec.language
        safe_write(
            config.samples_dir,
            Path(result.spec.id) / f"poc.{ext}",
            result.code,
        )
        safe_write(
            config.samples_dir,
            Path(result.spec.id) / "result.txt",
            f"exit_code: {result.exit_code}\n\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}\n",
        )
    console.print(f"[green]Draft   →[/green] {draft_path}")
    console.print(f"[green]Sources →[/green] {sources_path}")


def _print_summary(state: BlogState) -> None:
    if not state.draft:
        console.print("[red]No draft was produced.[/red]")
        return
    console.print(Panel("Final draft preview", border_style="green"))
    console.print(Markdown(state.draft[:1500] + ("\n…\n" if len(state.draft) > 1500 else "")))


# ---------------------------------------------------------------------------
# UI launcher
# ---------------------------------------------------------------------------


@app.command("ui")
def ui(
    host: Annotated[str, typer.Option("--host", help="Bind host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port.")] = 8000,
    reload: Annotated[
        bool, typer.Option("--reload", help="Enable uvicorn auto-reload.")
    ] = False,
) -> None:
    """Launch the chat-style web UI (FastAPI + WebSocket on localhost)."""
    try:
        from ui.server import run as run_ui
    except ImportError as exc:
        console.print(
            "[red]UI dependencies are missing.[/red] Install them with:\n"
            "  uv pip install fastapi websockets"
        )
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]Starting blog-writer UI[/green] → http://{host}:{port}/  (Ctrl+C to stop)"
    )
    run_ui(host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
