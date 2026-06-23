"""The blog-writer multi-agent pipeline.

This module wires the 9 agents together into a sequential state machine with
two human-in-the-loop checkpoints (angle pick + plan approval) and one
revision loop (Writer ⇄ Critic). Each stage:

1. Reads from `BlogState`.
2. Calls the agent (or runs a deterministic helper for parsing / IO).
3. Appends its contribution to `BlogState`.
4. Returns the updated state.

Stages are kept as small `async` functions rather than `WorkflowBuilder`
nodes so the pipeline reads top-to-bottom. We can graduate to `WorkflowBuilder`
once we need checkpointing / visualization / parallel sub-graphs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework import Agent
from pydantic import BaseModel, Field

from blog_writer.agents import (
    build_critic_agent,
    build_diagrammer_agent,
    build_fact_checker_agent,
    build_ideation_agent,
    build_orchestrator_agent,
    build_planner_agent,
    build_poc_builder_agent,
    build_writer_agent,
)
from blog_writer.config import AppConfig
from blog_writer.models import ModelMap, load_model_map
from blog_writer.tools.bing_search import bing_search_stub, search_web
from blog_writer.tools.code_sandbox import run_in_sandbox
from blog_writer.tools.deep_research import deep_research
from blog_writer.tools.excalidraw import (
    DiagramSpec,
    parse_diagram_spec,
    render_diagram,
    spec_from_sections,
)
from blog_writer.tools.learn_mcp import load_learn_scopes, search_learn, search_learn_stub
from blog_writer.workflows.state import (
    BlogState,
    Citation,
    CriticVerdict,
    FactCheckFinding,
    Outline,
    PoCResult,
    PoCSpec,
    Section,
)

# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------


HumanCallback = Callable[[str, list[str] | None], Awaitable[str]]
"""Async callback for human-in-the-loop checkpoints.

Receives `(prompt, optional choices)` and returns the user's response. The CLI
provides a Rich-based interactive implementation; tests provide a canned one;
`--autonomous` mode bypasses the callback entirely.
"""

EventCallback = Callable[[dict[str, Any]], None]
"""Callback for structured pipeline events (used by the UI).

Each event is a dict with at least a ``type`` key. Common types:

* ``stage_start`` / ``stage_end`` — ``{type, stage, label}``
* ``log``                          — ``{type, message}``
* ``angles``                       — ``{type, angles: list[str]}``
* ``outline``                      — ``{type, title, sections, pocs}``
* ``poc_result``                   — ``{type, id, exit_code, attempts}``
* ``diagram``                      — ``{type, title, excalidraw, mermaid}``
* ``critic``                       — ``{type, round, total, verdict, feedback}``
* ``draft``                        — ``{type, markdown, iteration}``
* ``done``                         — ``{type, final_verdict}``

The CLI doesn't use this callback (it sticks with the simpler ``progress``
hook); the FastAPI/WebSocket UI in ``ui/server.py`` does.
"""


def _make_emit(on_event: EventCallback | None) -> Callable[..., None]:
    """Return a no-op-if-unset event emitter for use inside the pipeline."""

    def emit(event_type: str, **kwargs: Any) -> None:
        if on_event is None:
            return
        try:
            on_event({"type": event_type, **kwargs})
        except Exception:  # noqa: BLE001 - never let the UI break the pipeline
            pass

    return emit


async def run_blog_pipeline(
    seed: str,
    *,
    config: AppConfig,
    models: ModelMap | None = None,
    on_human_input: HumanCallback | None = None,
    autonomous: bool = False,
    progress: Callable[[str], None] | None = None,
    on_event: EventCallback | None = None,
    extra_instructions: str | None = None,
    suggested_toc: str | None = None,
    reference_draft: str | None = None,
) -> BlogState:
    """Run the full pipeline and return the final state.

    ``extra_instructions`` and ``suggested_toc`` are optional user-provided
    steering knobs surfaced by the UI. They are appended to the Planner's
    input so the user can pre-seed a table of contents or impose
    constraints (tone, length, audience, etc.) without modifying prompts.

    ``reference_draft`` is an optional existing Markdown draft the user
    uploaded. It is surfaced to the Planner, Writer, and Critic so the agents
    can mine it for ideas, reuse what works, and challenge what doesn't.
    """
    models = models or load_model_map()
    state = BlogState(seed=seed)
    state.extra_instructions = extra_instructions or ""
    state.suggested_toc = suggested_toc or ""
    state.reference_draft = reference_draft or ""
    _log = progress or (lambda _msg: None)
    emit = _make_emit(on_event)

    def log_and_emit(msg: str) -> None:
        _log(msg)
        emit("log", message=msg)

    # 1. Ideation
    emit("stage_start", stage="ideation", label="Generating angles")
    log_and_emit("Generating angles…")
    state = await _ideate(state, config=config, models=models)
    log_and_emit(f"Generated {len(state.angles)} angles.")
    emit("angles", angles=list(state.angles))
    emit("stage_end", stage="ideation")

    # 2. Human checkpoint: pick angle
    emit("stage_start", stage="pick_angle", label="Picking angle")
    state.angle = await _pick_angle(
        state,
        on_human_input=on_human_input,
        autonomous=autonomous,
    )
    log_and_emit(f"Angle: {state.angle}")
    emit("angle_picked", angle=state.angle)
    emit("stage_end", stage="pick_angle")

    # 3. Internal knowledge + external research
    emit("stage_start", stage="internal_knowledge", label="MS Learn (curated scope)")
    log_and_emit("Gathering internal best practices (MS Learn)…")
    state = await _internal_knowledge(state, config=config, models=models)
    log_and_emit(f"Got {len(state.internal_hits)} in-scope Learn hits.")
    emit(
        "citations",
        kind="internal",
        items=[_citation_to_dict(c) for c in state.internal_hits],
    )
    emit("stage_end", stage="internal_knowledge")

    emit("stage_start", stage="research", label="External research (broad)")
    log_and_emit("Gathering external research…")
    state = await _external_research(state, config=config, models=models)
    log_and_emit(f"Got {len(state.external_hits)} external hits.")
    emit(
        "citations",
        kind="external",
        items=[_citation_to_dict(c) for c in state.external_hits],
    )
    emit("stage_end", stage="research")

    # 4. Plan
    emit("stage_start", stage="planner", label="Drafting outline")
    log_and_emit("Drafting outline…")
    state = await _plan(state, config=config, models=models)
    if state.outline:
        emit(
            "outline",
            title=state.outline.title,
            sections=[s.heading for s in state.outline.sections],
            pocs=[
                {"id": p.id, "description": p.description, "language": p.language}
                for p in state.outline.pocs
            ],
        )
    emit("stage_end", stage="planner")

    # 5. Human checkpoint: approve plan
    emit("stage_start", stage="approve_plan", label="Plan approval")
    state.plan_approved = await _approve_plan(
        state,
        on_human_input=on_human_input,
        autonomous=autonomous,
    )
    if not state.plan_approved:
        log_and_emit("Plan not approved. Aborting.")
        emit("stage_end", stage="approve_plan", approved=False)
        emit("done", final_verdict="rejected_at_plan")
        return state
    emit("stage_end", stage="approve_plan", approved=True)

    # 6. PoCs
    poc_count = len(state.outline.pocs) if state.outline else 0
    emit("stage_start", stage="poc_builder", label=f"Building {poc_count} PoC(s)")
    log_and_emit(f"Building {poc_count} PoC(s)…")
    state = await _build_pocs(state, config=config, models=models)
    for r in state.poc_results:
        emit(
            "poc_result",
            id=r.spec.id,
            exit_code=r.exit_code,
            attempts=r.attempts,
            stdout=(r.stdout or "")[:1000],
        )
    emit("stage_end", stage="poc_builder")

    # 6b. Architecture diagram (Excalidraw + embeddable Mermaid)
    if config.diagrams:
        emit("stage_start", stage="diagrammer", label="Drawing architecture")
        log_and_emit("Drawing architecture diagram…")
        state = await _make_diagram(state, config=config, models=models)
        emit(
            "diagram",
            title=state.diagram_title,
            excalidraw=state.diagram_excalidraw,
            mermaid=state.diagram_mermaid,
        )
        emit("stage_end", stage="diagrammer")

    # 7. Writer ⇄ Critic revision loop
    emit("stage_start", stage="writer", label="Writing draft")
    log_and_emit("Writing draft…")
    state = await _write_draft(state, config=config, models=models)
    emit("draft", markdown=state.draft or "", iteration=state.iteration)
    emit("stage_end", stage="writer")

    emit("stage_start", stage="fact_checker", label="Fact-checking")
    state = await _fact_check(state, config=config, models=models)
    emit(
        "fact_findings",
        items=[
            {"section": f.section, "status": f.status, "claim": f.claim}
            for f in state.fact_findings
        ],
    )
    emit("stage_end", stage="fact_checker")

    emit("stage_start", stage="critic", label="Critic review")
    state = await _critic_loop(
        state, config=config, models=models, log=log_and_emit, emit=emit
    )
    emit("stage_end", stage="critic")

    # 8. Final orchestrator review
    emit("stage_start", stage="final_review", label="Final review")
    log_and_emit("Final review…")
    state = await _final_review(state, config=config, models=models)
    emit("stage_end", stage="final_review")

    emit("draft", markdown=state.draft or "", iteration=state.iteration)
    emit("done", final_verdict=state.final_verdict or "approved")
    return state


# -----------------------------------------------------------------------------
# Revise — entry point for the UI's free-form edit loop
# -----------------------------------------------------------------------------


async def revise_blog_post(
    state: BlogState,
    instruction: str,
    *,
    config: AppConfig,
    models: ModelMap | None = None,
    on_event: EventCallback | None = None,
    run_fact_check: bool = True,
    run_critic: bool = True,
) -> BlogState:
    """Apply a free-form user instruction to the current draft.

    Runs the Writer with the user's instruction injected as a revision
    note, then (optionally) re-runs the Fact-Checker and one Critic pass
    so the draft is still grounded and scored. Returns the updated state;
    the caller is responsible for persisting it.

    Use this from the UI's edit-mode loop: each user turn calls this once
    and the new ``state.draft`` is rendered.
    """
    if not state.draft:
        raise ValueError("revise_blog_post: state has no draft yet — run the pipeline first.")
    if not instruction.strip():
        raise ValueError("revise_blog_post: instruction is empty.")

    models = models or load_model_map()
    emit = _make_emit(on_event)

    # Stash the instruction as if the critic had asked for it — the Writer
    # already knows how to consume `state.latest_critic.feedback` on the
    # next pass, so we don't need a special prompt path.
    synthetic_verdict = CriticVerdict(
        total=0,
        verdict="revise",
        scores={},
        feedback=[f"User revision request: {instruction.strip()}"],
    )
    state.critic_verdicts.append(synthetic_verdict)

    emit("stage_start", stage="writer", label="Applying revision")
    state = await _write_draft(state, config=config, models=models)
    emit("draft", markdown=state.draft or "", iteration=state.iteration)
    emit("stage_end", stage="writer")

    if run_fact_check:
        emit("stage_start", stage="fact_checker", label="Re-checking facts")
        state = await _fact_check(state, config=config, models=models)
        emit(
            "fact_findings",
            items=[
                {"section": f.section, "status": f.status, "claim": f.claim}
                for f in state.fact_findings
            ],
        )
        emit("stage_end", stage="fact_checker")

    if run_critic:
        emit("stage_start", stage="critic", label="Re-scoring draft")
        verdict = await _critic_pass(state, config=config, models=models)
        state.critic_verdicts.append(verdict)
        emit(
            "critic",
            round=len(state.critic_verdicts),
            total=verdict.total,
            verdict=verdict.verdict,
            feedback=list(verdict.feedback),
        )
        emit("stage_end", stage="critic")

    emit("revision_done", iteration=state.iteration)
    return state


def _citation_to_dict(c: Citation) -> dict[str, str]:
    return {
        "key": c.key,
        "kind": c.kind,
        "title": c.title,
        "url": c.url,
        "summary": c.summary,
    }


# -----------------------------------------------------------------------------
# Stage 1 — Ideation
# -----------------------------------------------------------------------------


_ANGLE_HEADING = re.compile(r"^##\s*\d+\.\s*(.+?)\s*$", re.MULTILINE)


async def _ideate(state: BlogState, *, config: AppConfig, models: ModelMap) -> BlogState:
    agent = build_ideation_agent(config, models)
    response = await _run_agent(agent, f"Seed topic: {state.seed}")
    text = _text_of(response)
    angles = _ANGLE_HEADING.findall(text)
    # Fallback: split by lines starting with "1." if regex didn't match.
    if not angles:
        angles = [
            line.lstrip("0123456789.* ").strip()
            for line in text.splitlines()
            if re.match(r"^\s*\d+[\.\)]", line)
        ]
    state.angles = angles or [f"Angle: {state.seed}"]
    return state


async def _pick_angle(
    state: BlogState,
    *,
    on_human_input: HumanCallback | None,
    autonomous: bool,
) -> str:
    if autonomous or not on_human_input:
        return state.angles[0]
    prompt = "Pick an angle for the post:"
    choice = await on_human_input(prompt, state.angles)
    return choice or state.angles[0]


# -----------------------------------------------------------------------------
# Stage 2 — Internal Knowledge (MS Learn MCP)
# -----------------------------------------------------------------------------


async def _internal_knowledge(
    state: BlogState, *, config: AppConfig, models: ModelMap
) -> BlogState:
    """Stage 2 — query MS Learn via MCP, scope-filter, build citations.

    The search is always done by a direct MCP call (no LLM in the loop) so
    citations are grounded in a real result set. In real mode, after the
    deterministic seed, the Internal Knowledge agent is asked to rank and
    summarise — it can also fetch full pages via ``microsoft_docs_fetch``.
    """
    scope = load_learn_scopes(config)
    query = state.angle or state.seed

    if config.stub:
        raw_hits = await search_learn_stub(query)
    else:
        raw_hits = await search_learn(query, url=config.ms_learn_mcp_url)
        # Network / MCP error or empty result — fall back to canned hits so
        # the rest of the pipeline still has something to work with.
        if not raw_hits:
            raw_hits = await search_learn_stub(query)

    hits = scope.filter_hits(raw_hits)
    # Keep the top N to bound LLM prompts downstream.
    hits = hits[: config.max_learn_hits]
    state.internal_hits = [
        Citation(
            key=f"L{i + 1}",
            kind="learn",
            title=h.title,
            url=h.url,
            summary=h.excerpt,
        )
        for i, h in enumerate(hits)
    ]
    return state


# -----------------------------------------------------------------------------
# Stage 3 — External Research (Bing grounding)
# -----------------------------------------------------------------------------


async def _external_research(
    state: BlogState, *, config: AppConfig, models: ModelMap
) -> BlogState:
    """Stage 3 — fill gaps with external sources (Tavily / Bing v7).

    Like Internal Knowledge, this uses a deterministic direct API call so
    citations are always grounded in a real search result. When no external
    search backend is configured (no TAVILY_API_KEY / BING_SEARCH_API_KEY),
    we fall back to the canned stub list so the writer always has *some*
    secondary references to balance the Learn-first citations.
    """
    query = state.angle or state.seed

    if config.stub:
        hits = await bing_search_stub(query)
    elif config.deep_research:
        # Agentic, Bing-grounded o3-deep-research pass. Produces a synthesized
        # report plus real citations; fall back to lightweight search/stub if
        # deep research is unconfigured or fails.
        report, hits = await deep_research(query)
        if report:
            state.research_report = report
        if not hits:
            hits = await search_web(query) or await bing_search_stub(query)
    else:
        hits = await search_web(query)
        if not hits:
            # No external backend configured (or call failed) — keep the
            # pipeline moving with the canned external list.
            hits = await bing_search_stub(query)

    state.external_hits = [
        Citation(
            key=f"E{i + 1}",
            kind="external",
            title=h["title"],
            url=h["url"],
            summary=h.get("snippet", ""),
        )
        for i, h in enumerate(hits)
    ]
    return state


# -----------------------------------------------------------------------------
# Stage 4 — Planning
# -----------------------------------------------------------------------------


async def _plan(state: BlogState, *, config: AppConfig, models: ModelMap) -> BlogState:
    agent = build_planner_agent(config, models)
    prompt = _planner_inputs(state)
    outline: Outline | None = None
    # Prefer structured output — the model frequently emits headings with
    # embedded colons (e.g. "Core Concepts: ...") which make free-form YAML
    # parse to an empty outline. A schema-constrained response avoids that.
    if not config.stub:
        try:
            result = await agent.run(prompt, options={"response_format": _PlannerOutput})
            parsed = getattr(result, "value", None)
            if isinstance(parsed, _PlannerOutput) and parsed.sections:
                outline = _outline_from_model(
                    parsed, fallback_title=state.angle or state.seed
                )
        except Exception:
            outline = None
    if outline is None:
        response = await _run_agent(agent, prompt)
        text = _text_of(response)
        outline = _parse_outline(text, fallback_title=state.angle or state.seed)
    state.outline = outline
    return state


async def _approve_plan(
    state: BlogState,
    *,
    on_human_input: HumanCallback | None,
    autonomous: bool,
) -> bool:
    if autonomous or not on_human_input:
        return True
    if not state.outline:
        return False
    summary = (
        f"Title: {state.outline.title}\n\n"
        f"Sections ({len(state.outline.sections)}):\n"
        + "\n".join(f"  - {s.heading}" for s in state.outline.sections)
        + f"\n\nPoCs: {len(state.outline.pocs)}"
    )
    answer = await on_human_input(
        f"Approve this plan?\n\n{summary}",
        ["approve", "reject"],
    )
    return (answer or "approve").lower().startswith("a")


# -----------------------------------------------------------------------------
# Stage 5 — PoC building
# -----------------------------------------------------------------------------


async def _build_pocs(state: BlogState, *, config: AppConfig, models: ModelMap) -> BlogState:
    if not state.outline:
        return state
    if not state.outline.pocs:
        return state
    results: list[PoCResult] = []
    for spec in state.outline.pocs:
        result = await _build_one_poc(spec, config=config, models=models)
        results.append(result)
    state.poc_results = results
    return state


async def _build_one_poc(
    spec: PoCSpec, *, config: AppConfig, models: ModelMap
) -> PoCResult:
    """Generate a PoC, run it in the sandbox, retry on failure up to N attempts.

    Each retry passes the previous attempt's stderr back to the agent so it
    can fix the bug. Stops as soon as the sandbox returns exit code 0 or we
    run out of attempts.
    """
    if config.stub:
        code = (
            '"""Stub PoC demonstrating the concept."""\n'
            'print("hello from stub PoC")\n'
        )
        sandbox_result = await run_in_sandbox(
            code, language=spec.language, config=config
        )
        return PoCResult(
            spec=spec,
            code=code,
            code_path=f"samples/{spec.id}/poc.py",
            exit_code=sandbox_result.exit_code,
            stdout=sandbox_result.stdout,
            stderr=sandbox_result.stderr,
            attempts=1,
            narrative=f"Demonstrates: {spec.description}",
        )

    agent = build_poc_builder_agent(config, models)
    max_attempts = max(1, config.max_poc_attempts)
    last_text = ""
    code = f"# {spec.description}\nprint('todo')\n"
    sandbox_result = None
    for attempt in range(1, max_attempts + 1):
        prompt = _poc_prompt(spec, attempt=attempt, previous=sandbox_result, previous_code=code)
        response = await _run_agent(agent, prompt)
        last_text = _text_of(response)
        extracted = _extract_first_code_block(last_text)
        if extracted:
            code = extracted
        sandbox_result = await run_in_sandbox(code, language=spec.language, config=config)
        if sandbox_result.exit_code == 0:
            break

    assert sandbox_result is not None  # loop runs at least once
    return PoCResult(
        spec=spec,
        code=code,
        code_path=f"samples/{spec.id}/poc.py",
        exit_code=sandbox_result.exit_code,
        stdout=sandbox_result.stdout,
        stderr=sandbox_result.stderr,
        attempts=attempt,
        narrative=last_text.split("```", maxsplit=1)[0].strip() or spec.description,
    )


def _poc_prompt(
    spec: PoCSpec,
    *,
    attempt: int,
    previous: Any | None,
    previous_code: str,
) -> str:
    base = (
        f"PoC spec:\n{spec}\n\n"
        "Generate the code (as a single fenced code block), then describe what it shows."
    )
    if attempt == 1 or previous is None or previous.exit_code == 0:
        return base
    # Retry: give the agent the previous attempt and its failure output so it
    # can fix the bug rather than starting from scratch.
    return (
        base
        + f"\n\nPrevious attempt {attempt - 1} failed (exit code {previous.exit_code}). "
        "Fix the bug and return a corrected sample.\n\n"
        f"Previous code:\n```{spec.language}\n{previous_code}\n```\n\n"
        f"stderr (truncated):\n{(previous.stderr or '')[:2000]}\n\n"
        f"stdout (truncated):\n{(previous.stdout or '')[:1000]}"
    )


# -----------------------------------------------------------------------------
# Stage 6b — Architecture diagram (Excalidraw + Mermaid)
# -----------------------------------------------------------------------------


async def _make_diagram(state: BlogState, *, config: AppConfig, models: ModelMap) -> BlogState:
    """Build an architecture diagram for the post.

    The diagrammer agent emits a small node/edge JSON spec; we render it
    deterministically into an editable ``.excalidraw`` scene plus an
    embeddable Mermaid flowchart. If the agent output can't be parsed we fall
    back to a spec derived from the outline sections so a diagram is always
    produced.
    """
    title = state.outline.title if state.outline else (state.angle or state.seed)
    sections = [s.heading for s in state.outline.sections] if state.outline else []

    spec: DiagramSpec | None = None
    if config.stub:
        spec = DiagramSpec(
            title=title or "Architecture",
            groups=[],
            nodes=[
                _diag_node("client", "Client"),
                _diag_node("pipeline", "Agent Pipeline"),
                _diag_node("learn", "MS Learn MCP"),
                _diag_node("draft", "Draft"),
            ],
            edges=[
                _diag_edge("client", "pipeline", "topic"),
                _diag_edge("pipeline", "learn", "grounding"),
                _diag_edge("pipeline", "draft"),
            ],
        )
    else:
        agent = build_diagrammer_agent(config, models)
        response = await _run_agent(agent, _diagram_inputs(state))
        spec = parse_diagram_spec(_text_of(response))

    if spec is None or not spec.ok:
        spec = spec_from_sections(title or "Architecture", sections)

    artifacts = render_diagram(spec)
    state.diagram_title = artifacts.title
    state.diagram_excalidraw = artifacts.excalidraw
    state.diagram_mermaid = artifacts.mermaid
    return state


def _diagram_inputs(state: BlogState) -> str:
    poc_lines = "\n".join(f"- {r.spec.id} ({r.spec.section})" for r in state.poc_results)
    return (
        f"Title: {state.outline.title if state.outline else state.angle}\n\n"
        f"Outline:\n{_outline_summary(state)}\n\n"
        f"PoCs:\n{poc_lines or '(none)'}\n\n"
        "Design the architecture diagram and return the JSON spec only."
    )


def _diag_node(node_id: str, label: str, group: str | None = None) -> Any:
    from blog_writer.tools.excalidraw import DiagramNode

    return DiagramNode(id=node_id, label=label, group=group)


def _diag_edge(source: str, target: str, label: str = "") -> Any:
    from blog_writer.tools.excalidraw import DiagramEdge

    return DiagramEdge(source=source, target=target, label=label)


# -----------------------------------------------------------------------------
# Stage 6 — Writing
# -----------------------------------------------------------------------------


async def _write_draft(state: BlogState, *, config: AppConfig, models: ModelMap) -> BlogState:
    agent = build_writer_agent(config, models)
    revision_note = ""
    if state.iteration > 0 and state.latest_critic:
        revision_note = (
            "\n\nRevise the previous draft to address the following critic feedback:\n"
            + "\n".join(f"- {item}" for item in state.latest_critic.feedback)
            + f"\n\nPrevious draft:\n{state.draft or ''}"
        )
    response = await _run_agent(agent, _writer_inputs(state) + revision_note)
    state.draft = _text_of(response)
    state.iteration += 1
    return state


# -----------------------------------------------------------------------------
# Stage 7 — Fact-check
# -----------------------------------------------------------------------------


async def _fact_check(state: BlogState, *, config: AppConfig, models: ModelMap) -> BlogState:
    if not state.draft:
        return state
    agent = build_fact_checker_agent(config, models)
    response = await _run_agent(
        agent,
        f"Draft:\n{state.draft}\n\nSources:\n{_citations_yaml(state)}",
    )
    text = _text_of(response)
    state.fact_findings = _parse_fact_findings(text)
    return state


# -----------------------------------------------------------------------------
# Stage 8 — Critic loop
# -----------------------------------------------------------------------------


async def _critic_loop(
    state: BlogState,
    *,
    config: AppConfig,
    models: ModelMap,
    log: Callable[[str], None],
    emit: Callable[..., None] | None = None,
) -> BlogState:
    _emit = emit or (lambda *a, **kw: None)
    for revision in range(config.max_revisions + 1):
        verdict = await _critic_pass(state, config=config, models=models)
        state.critic_verdicts.append(verdict)
        log(f"Critic round {revision + 1}: total={verdict.total} -> {verdict.verdict}")
        _emit(
            "critic",
            round=revision + 1,
            total=verdict.total,
            verdict=verdict.verdict,
            feedback=list(verdict.feedback),
        )
        if verdict.verdict == "accept" or verdict.total >= config.critic_threshold:
            return state
        if revision >= config.max_revisions:
            log(
                f"Hit max revisions ({config.max_revisions}); accepting current draft."
            )
            return state
        log("Revising draft…")
        state = await _write_draft(state, config=config, models=models)
        _emit("draft", markdown=state.draft or "", iteration=state.iteration)
        state = await _fact_check(state, config=config, models=models)
    return state


async def _critic_pass(
    state: BlogState, *, config: AppConfig, models: ModelMap
) -> CriticVerdict:
    agent = build_critic_agent(config, models)
    response = await _run_agent(
        agent,
        (
            f"Threshold: {config.critic_threshold}\n\n"
            f"Outline:\n{_outline_summary(state)}\n\n"
            f"Draft:\n{state.draft or ''}\n\n"
            f"Fact-check findings ({len(state.fact_findings)}):\n"
            + "\n".join(
                f"- [{f.status}] {f.section}: {f.claim}" for f in state.fact_findings
            )
            + _reference_block(
                state,
                "Use it as a benchmark: if our draft is weaker than this reference on "
                "any dimension, call that out and challenge it specifically.",
            )
        ),
    )
    text = _text_of(response)
    return _parse_critic_verdict(text, threshold=config.critic_threshold)


# -----------------------------------------------------------------------------
# Stage 9 — Final orchestrator review
# -----------------------------------------------------------------------------


async def _final_review(state: BlogState, *, config: AppConfig, models: ModelMap) -> BlogState:
    agent = build_orchestrator_agent(config, models)
    response = await _run_agent(
        agent,
        (
            f"Draft (excerpt):\n{(state.draft or '')[:2000]}\n\n"
            f"Sources:\n{_citations_yaml(state)}\n\n"
            f"PoCs: {[r.spec.id for r in state.poc_results]}"
        ),
    )
    text = _text_of(response)
    first_line = text.splitlines()[0].strip().upper() if text else ""
    state.final_verdict = "approved" if first_line.startswith("APPROVED") else "revise"
    state.final_notes = text
    return state


# =============================================================================
# Helpers — agent IO, parsing, formatting
# =============================================================================


async def _run_agent(agent: Agent, user_message: str) -> Any:
    """Tiny wrapper around `agent.run` to keep call sites concise."""
    return await agent.run(user_message)


def _text_of(response: Any) -> str:
    """Pull plain text out of whatever shape the agent returned."""
    # Agent Framework's AgentResponse exposes `.text` or `.messages[-1].text`.
    text = getattr(response, "text", None)
    if isinstance(text, str) and text:
        return text
    messages = getattr(response, "messages", None) or []
    if messages:
        last = messages[-1]
        last_text = getattr(last, "text", None)
        if isinstance(last_text, str):
            return last_text
        contents = getattr(last, "contents", None) or []
        chunks: list[str] = []
        for c in contents:
            t = getattr(c, "text", None)
            if isinstance(t, str):
                chunks.append(t)
            elif isinstance(c, str):
                chunks.append(c)
        if chunks:
            return "\n".join(chunks)
    return str(response)


def _parse_citations(text: str, *, kind: str, prefix: str) -> list[Citation]:
    """Extract citations from agent output.

    We accept the YAML format the prompt asks for; if it isn't parseable we
    fall back to scanning for URLs and bullet points so the pipeline keeps
    moving.
    """
    citations: list[Citation] = []
    try:
        import yaml

        # Try direct YAML parse first (works when the model returned clean YAML).
        data = yaml.safe_load(text)
        if isinstance(data, dict) and isinstance(data.get("hits"), list):
            for i, hit in enumerate(data["hits"]):
                if not isinstance(hit, dict):
                    continue
                citations.append(
                    Citation(
                        key=f"{prefix}{i + 1}",
                        kind=kind,  # type: ignore[arg-type]
                        title=str(hit.get("title", "Untitled")),
                        url=str(hit.get("url", "")),
                        summary=str(hit.get("summary", "")),
                    )
                )
            if citations:
                return citations
    except Exception:
        pass

    # Fallback: grep markdown link bullets.
    link_re = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
    for i, match in enumerate(link_re.finditer(text)):
        citations.append(
            Citation(
                key=f"{prefix}{i + 1}",
                kind=kind,  # type: ignore[arg-type]
                title=match.group(1),
                url=match.group(2),
                summary="",
            )
        )
    return citations


def _reference_block(state: BlogState, guidance: str) -> str:
    """Render the user's uploaded reference draft for an agent prompt.

    Returns an empty string when no reference draft was provided. ``guidance``
    tells the specific agent how to use it (consider vs. challenge).
    """
    text = (state.reference_draft or "").strip()
    if not text:
        return ""
    # Cap the size so a huge upload can't blow the context window.
    excerpt = text[:8000]
    if len(text) > 8000:
        excerpt += "\n\n…(reference draft truncated)…"
    return (
        "\n\nReference draft supplied by the user (an existing post on a related "
        "topic). " + guidance + "\n--- BEGIN REFERENCE DRAFT ---\n"
        + excerpt
        + "\n--- END REFERENCE DRAFT ---"
    )


def _planner_inputs(state: BlogState) -> str:
    extras: list[str] = []
    if state.suggested_toc.strip():
        extras.append(
            "User-suggested table of contents (use as a strong starting point, "
            "but feel free to refine):\n" + state.suggested_toc.strip()
        )
    if state.extra_instructions.strip():
        extras.append("Additional user instructions:\n" + state.extra_instructions.strip())
    extras_block = ("\n\n" + "\n\n".join(extras)) if extras else ""
    report_block = (
        "\n\nDeep-research briefing (synthesized, Bing-grounded):\n"
        + state.research_report.strip()
        if state.research_report.strip()
        else ""
    )
    return (
        f"Angle: {state.angle}\n\n"
        f"Internal best practices ({len(state.internal_hits)} hits):\n"
        + _citations_yaml(state, only="learn")
        + f"\n\nExternal research ({len(state.external_hits)} hits):\n"
        + _citations_yaml(state, only="external")
        + report_block
        + extras_block
        + _reference_block(
            state,
            "Use it to shape the outline: keep the angles that work, fix the gaps, "
            "and don't simply reproduce its structure.",
        )
        + "\n\nReturn the YAML outline + PoC list as specified."
    )


def _writer_inputs(state: BlogState) -> str:
    pocs_summary = "\n".join(
        f"- {r.spec.id} ({r.spec.section}): exit={r.exit_code}, "
        f"stdout_snippet={r.stdout[:120]!r}"
        for r in state.poc_results
    )
    extras = ""
    if state.extra_instructions.strip():
        extras = (
            "\n\nAdditional user instructions to honor while writing:\n"
            + state.extra_instructions.strip()
        )
    report_block = ""
    if state.research_report.strip():
        report_block = (
            "\n\nDeep-research briefing (synthesized, Bing-grounded — cite via the "
            "external [E#] sources above):\n" + state.research_report.strip()
        )
    diagram_block = ""
    if state.diagram_mermaid.strip():
        diagram_block = (
            "\n\nArchitecture diagram — embed this Mermaid block verbatim (inside a "
            "```mermaid fence) in the most relevant section, with one sentence of "
            "lead-in explaining what it shows:\n"
            f"```mermaid\n{state.diagram_mermaid.strip()}\n```"
        )
    return (
        f"Outline:\n{_outline_summary(state)}\n\n"
        f"Citations (use Learn first):\n{_citations_yaml(state)}\n\n"
        f"PoC results:\n{pocs_summary}"
        + report_block
        + diagram_block
        + extras
        + _reference_block(
            state,
            "Treat it as raw material, not a template: borrow strong phrasing or "
            "examples, but improve on its arguments and avoid copying it wholesale.",
        )
        + "\n\nWrite the full Markdown draft now."
    )


def _citations_yaml(state: BlogState, *, only: str | None = None) -> str:
    rows: list[Citation]
    if only == "learn":
        rows = list(state.internal_hits)
    elif only == "external":
        rows = list(state.external_hits)
    else:
        rows = state.all_citations
    if not rows:
        return "(none)"
    return "\n".join(
        f"- [{c.key}] {c.title} — {c.url}\n    {c.summary}" for c in rows
    )


def _outline_summary(state: BlogState) -> str:
    if not state.outline:
        return "(no outline)"
    lines = [f"Title: {state.outline.title}", f"Summary: {state.outline.summary}", ""]
    for s in state.outline.sections:
        lines.append(f"## {s.heading}")
        lines.append(f"  argues: {s.argues}")
        if s.leans_on:
            lines.append(f"  leans_on: {', '.join(s.leans_on)}")
    if state.outline.pocs:
        lines.append("\nPoCs:")
        for p in state.outline.pocs:
            lines.append(f"- {p.id} ({p.section}): {p.description}")
    return "\n".join(lines)


class _PlannerSection(BaseModel):
    heading: str = ""
    argues: str = ""
    leans_on: list[str] = Field(default_factory=list)


class _PlannerPoC(BaseModel):
    id: str = "poc"
    section: str = ""
    description: str = ""
    language: str = "python"
    sandbox: str = "local"


class _PlannerOutput(BaseModel):
    """Schema for the Planner's structured (response_format) output."""

    title: str = ""
    summary: str = ""
    sections: list[_PlannerSection] = Field(default_factory=list)
    pocs: list[_PlannerPoC] = Field(default_factory=list)


def _outline_from_model(parsed: _PlannerOutput, *, fallback_title: str) -> Outline:
    return Outline(
        title=parsed.title or fallback_title,
        summary=parsed.summary or "",
        sections=[
            Section(heading=s.heading, argues=s.argues, leans_on=list(s.leans_on))
            for s in parsed.sections
            if s.heading
        ],
        pocs=[
            PoCSpec(
                id=p.id,
                section=p.section,
                description=p.description,
                language=p.language,
                sandbox=p.sandbox,  # type: ignore[arg-type]
            )
            for p in parsed.pocs
        ],
    )


def _parse_outline(text: str, *, fallback_title: str) -> Outline:
    """Parse the Planner's YAML output into an `Outline`.

    Falls back to a one-section, no-PoC outline if parsing fails — the pipeline
    must keep moving even when the model returns malformed YAML.
    """
    try:
        import yaml

        # Models frequently wrap the YAML in a ```yaml ... ``` fence even when
        # asked not to; that breaks yaml.safe_load. Strip the fence first.
        payload = _extract_first_code_block(text) or text
        data = yaml.safe_load(payload)
        if isinstance(data, dict):
            sections = [
                Section(
                    heading=str(s.get("heading", "Untitled")),
                    argues=str(s.get("argues", "")),
                    leans_on=[str(x) for x in (s.get("leans_on") or [])],
                )
                for s in (data.get("sections") or [])
                if isinstance(s, dict)
            ]
            pocs = [
                PoCSpec(
                    id=str(p.get("id", "poc")),
                    section=str(p.get("section", "")),
                    description=str(p.get("description", "")),
                    language=str(p.get("language", "python")),
                    sandbox=str(p.get("sandbox", "local")),  # type: ignore[arg-type]
                )
                for p in (data.get("pocs") or [])
                if isinstance(p, dict)
            ]
            return Outline(
                title=str(data.get("title") or fallback_title),
                summary=str(data.get("summary") or ""),
                sections=sections,
                pocs=pocs,
            )
    except Exception:
        pass
    return Outline(title=fallback_title, summary="", sections=[], pocs=[])


def _parse_fact_findings(text: str) -> list[FactCheckFinding]:
    try:
        import yaml

        data = yaml.safe_load(text)
        if isinstance(data, dict):
            findings = []
            for f in data.get("findings") or []:
                if not isinstance(f, dict):
                    continue
                findings.append(
                    FactCheckFinding(
                        section=str(f.get("section", "")),
                        claim=str(f.get("claim", "")),
                        status=str(f.get("status", "missing-citation")),  # type: ignore[arg-type]
                        citation=(f.get("citation") or None),
                        suggestion=(f.get("suggestion") or None),
                    )
                )
            return findings
    except Exception:
        pass
    return []


def _parse_critic_verdict(text: str, *, threshold: int) -> CriticVerdict:
    """Extract a CriticVerdict from JSON; tolerate fenced blocks and prose."""
    json_text = _extract_json_object(text)
    try:
        data = json.loads(json_text) if json_text else {}
    except json.JSONDecodeError:
        data = {}
    total_raw = data.get("total")
    try:
        total = int(total_raw) if total_raw is not None else 0
    except (TypeError, ValueError):
        total = 0
    raw_verdict = str(data.get("verdict") or "").lower()
    if raw_verdict not in {"accept", "revise"}:
        raw_verdict = "accept" if total >= threshold else "revise"
    feedback = [str(x) for x in (data.get("feedback") or [])]
    scores = {str(k): int(v) for k, v in (data.get("scores") or {}).items() if isinstance(v, int)}
    return CriticVerdict(
        total=total,
        verdict=raw_verdict,  # type: ignore[arg-type]
        feedback=feedback,
        scores=scores,
    )


def _extract_first_code_block(text: str) -> str | None:
    match = re.search(r"```(?:[a-zA-Z]+)?\n(.+?)```", text, re.DOTALL)
    return match.group(1) if match else None


def _extract_json_object(text: str) -> str | None:
    # Prefer fenced ```json blocks; otherwise grab the first {...} that parses.
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    return brace.group(0) if brace else None


def _summarise_internal_gap(state: BlogState) -> str:
    if not state.internal_hits:
        return (
            "No in-scope Microsoft Learn hits were found — please cover the topic "
            "broadly from authoritative external sources."
        )
    titles = "; ".join(c.title for c in state.internal_hits[:5])
    return f"Learn already covers: {titles}. Find external sources that add specifics or recency."


# =============================================================================
# Improve — enhance an existing draft (find sources, recommend, re-cite)
# =============================================================================

_H1_HEADING = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_H2_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# Headings that don't describe the topic — skip them when building queries.
_BOILERPLATE_HEADINGS = {
    "sources",
    "references",
    "try it yourself",
    "architecture at a glance",
}


def _draft_title(draft: str) -> str:
    """The first H1 of a Markdown draft, or '' if there isn't one."""
    match = _H1_HEADING.search(draft or "")
    return match.group(1).strip() if match else ""


def _draft_queries(draft: str, *, max_queries: int = 6) -> list[str]:
    """Search queries derived from the draft: the title, then title+heading pairs.

    Used to find sources keyed off the draft's *own* subject matter rather than a
    seed topic. Boilerplate headings (Sources, etc.) are skipped.
    """
    title = _draft_title(draft)
    headings = [
        h.strip()
        for h in _H2_HEADING.findall(draft or "")
        if h.strip().lower() not in _BOILERPLATE_HEADINGS
        and not h.strip().lower().startswith("what ")
    ]
    queries: list[str] = []
    if title:
        queries.append(title)
    for heading in headings:
        queries.append(f"{title} {heading}".strip() if title else heading)

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower()
        if q and key not in seen:
            seen.add(key)
            out.append(q)
        if len(out) >= max_queries:
            break
    return out or [title or "Azure"]


async def _gather_learn_for_draft(
    state: BlogState, *, config: AppConfig, max_hits: int
) -> None:
    """Multi-query MS Learn search keyed off the draft; scope-filter + dedupe."""
    scope = load_learn_scopes(config)
    collected: list[Citation] = []
    seen: set[str] = set()
    for query in _draft_queries(state.draft or ""):
        if config.stub:
            raw_hits = await search_learn_stub(query)
        else:
            raw_hits = await search_learn(query, url=config.ms_learn_mcp_url)
            if not raw_hits:
                raw_hits = await search_learn_stub(query)
        for hit in scope.filter_hits(raw_hits):
            if hit.url in seen:
                continue
            seen.add(hit.url)
            collected.append(
                Citation(
                    key=f"L{len(collected) + 1}",
                    kind="learn",
                    title=hit.title,
                    url=hit.url,
                    summary=hit.excerpt,
                )
            )
            if len(collected) >= max_hits:
                break
        if len(collected) >= max_hits:
            break
    state.internal_hits = collected


async def _gather_external_for_draft(
    state: BlogState, *, config: AppConfig, max_hits: int
) -> None:
    """Find external sources for the draft via deep research (or web search)."""
    title = _draft_title(state.draft or "")
    topic = state.seed or title or (state.angle or "")
    if config.stub:
        hits = await bing_search_stub(topic)
    elif config.deep_research:
        areas = "; ".join(_draft_queries(state.draft or "")[1:]) or topic
        query = (
            "Find authoritative, up-to-date sources (Microsoft Learn first) to "
            f"support and fact-check a technical blog post titled '{title}'. "
            f"Key areas to verify: {areas}."
        )
        report, hits = await deep_research(query)
        if report:
            state.research_report = report
        if not hits:
            hits = await search_web(topic) or await bing_search_stub(topic)
    else:
        hits = await search_web(topic)
        if not hits:
            hits = await bing_search_stub(topic)

    learn_urls = {c.url for c in state.internal_hits}
    collected: list[Citation] = []
    seen: set[str] = set()
    for hit in hits:
        url = hit.get("url", "")
        if not url or url in seen or url in learn_urls:
            continue
        seen.add(url)
        collected.append(
            Citation(
                key=f"E{len(collected) + 1}",
                kind="external",
                title=hit.get("title", url),
                url=url,
                summary=hit.get("snippet", ""),
            )
        )
        if len(collected) >= max_hits:
            break
    state.external_hits = collected


def _improve_writer_inputs(state: BlogState) -> str:
    findings = (
        "\n".join(
            f"- [{f.status}] {f.section}: {f.claim}"
            + (f" (suggestion: {f.suggestion})" if f.suggestion else "")
            for f in state.fact_findings
        )
        or "(none)"
    )
    recs = (
        "\n".join(f"- {item}" for item in state.latest_critic.feedback)
        if state.latest_critic and state.latest_critic.feedback
        else "(none)"
    )
    report_block = ""
    if state.research_report.strip():
        report_block = (
            "\n\nDeep-research briefing (synthesized, Bing-grounded — cite via the "
            "external [E#] sources):\n" + state.research_report.strip()
        )
    return (
        "You are improving an EXISTING draft, not writing a new one. Preserve the "
        "author's structure, voice, headings, and any code or mermaid blocks. Make "
        "targeted improvements: tighten prose, fix issues raised below, and — most "
        "importantly — weave in citations from the sources below as footnotes "
        "([^L#] for Microsoft Learn first, [^E#] for external), then build or "
        "extend the `## Sources` section to list them. Only cite the sources "
        "provided; never invent a source or URL. Return the COMPLETE improved "
        "Markdown document.\n\n"
        f"Sources (use Learn first):\n{_citations_yaml(state)}\n\n"
        f"Fact-check findings to address:\n{findings}\n\n"
        f"Reviewer recommendations to address:\n{recs}"
        + report_block
        + f"\n\nExisting draft:\n{state.draft or ''}"
    )


def build_review_report(state: BlogState) -> str:
    """A human-readable Markdown review: recommendations, fact-check, sources."""
    title = _draft_title(state.draft or "") or state.angle or state.seed
    crit = state.latest_critic
    lines = [f"# Review — {title}", ""]
    if crit:
        lines += [f"**Critic score:** {crit.total} → {crit.verdict}", ""]
        if crit.scores:
            lines.append("**Scores by criterion:**")
            lines += [f"- {k}: {v}" for k, v in crit.scores.items()]
            lines.append("")
    lines += ["## Recommended improvements", ""]
    lines += [f"- {f}" for f in (crit.feedback if crit else [])] or ["- (none)"]
    lines += ["", "## Fact-check findings", ""]
    if state.fact_findings:
        for f in state.fact_findings:
            sug = f" — _{f.suggestion}_" if f.suggestion else ""
            lines.append(f"- **[{f.status}]** {f.section}: {f.claim}{sug}")
    else:
        lines.append("- (none)")
    lines += ["", "## Sources found", "", "### Microsoft Learn", ""]
    lines += (
        [f"- [{c.title}]({c.url})" for c in state.internal_hits]
        if state.internal_hits
        else ["- (none)"]
    )
    lines += ["", "### External", ""]
    lines += (
        [f"- [{c.title}]({c.url})" for c in state.external_hits]
        if state.external_hits
        else ["- (none)"]
    )
    if state.research_report.strip():
        lines += ["", "## Deep-research briefing", "", state.research_report.strip()]
    return "\n".join(lines) + "\n"


async def improve_blog_post(
    draft: str,
    *,
    config: AppConfig,
    models: ModelMap | None = None,
    topic: str | None = None,
    on_event: EventCallback | None = None,
    progress: Callable[[str], None] | None = None,
    rewrite: bool = True,
    run_fact_check: bool = True,
) -> BlogState:
    """Improve an existing draft: find sources, recommend changes, re-cite.

    Unlike :func:`run_blog_pipeline` (seed → new post), this takes a finished
    draft and:

    1. Finds Microsoft Learn + external/deep-research sources keyed off the
       draft's *own* title and section headings.
    2. Fact-checks and critiques the draft against those sources.
    3. (When ``rewrite``) re-runs the Writer to weave in Learn-first footnote
       citations and address the recommendations, preserving the author's
       structure and voice.

    Returns the updated state; the caller persists it. ``state.latest_critic``
    holds the recommendations and :func:`build_review_report` renders them.
    """
    if not draft or not draft.strip():
        raise ValueError("improve_blog_post: draft is empty.")

    models = models or load_model_map()
    title = _draft_title(draft)
    state = BlogState(seed=topic or title or "untitled draft")
    state.draft = draft
    state.angle = title or state.seed
    _log = progress or (lambda _msg: None)
    emit = _make_emit(on_event)

    def log_and_emit(msg: str) -> None:
        _log(msg)
        emit("log", message=msg)

    # 1. Internal knowledge — MS Learn, keyed off the draft.
    emit("stage_start", stage="internal_knowledge", label="MS Learn (curated scope)")
    log_and_emit("Finding Microsoft Learn sources for the draft…")
    await _gather_learn_for_draft(
        state, config=config, max_hits=max(config.max_learn_hits, 8)
    )
    log_and_emit(f"Found {len(state.internal_hits)} in-scope Learn sources.")
    emit("citations", kind="internal", items=[_citation_to_dict(c) for c in state.internal_hits])
    emit("stage_end", stage="internal_knowledge")

    # 2. External / deep research.
    label = "Deep research (Bing-grounded)" if config.deep_research else "External research"
    emit("stage_start", stage="research", label=label)
    log_and_emit(f"{label}…")
    await _gather_external_for_draft(state, config=config, max_hits=config.max_learn_hits)
    log_and_emit(f"Found {len(state.external_hits)} external sources.")
    emit("citations", kind="external", items=[_citation_to_dict(c) for c in state.external_hits])
    emit("stage_end", stage="research")

    # 3. Fact-check the existing draft against the new sources.
    if run_fact_check:
        emit("stage_start", stage="fact_checker", label="Fact-checking")
        log_and_emit("Fact-checking the draft against the sources…")
        state = await _fact_check(state, config=config, models=models)
        emit(
            "fact_findings",
            items=[
                {"section": f.section, "status": f.status, "claim": f.claim}
                for f in state.fact_findings
            ],
        )
        emit("stage_end", stage="fact_checker")

    # 4. Critic → improvement recommendations.
    emit("stage_start", stage="critic", label="Reviewing")
    log_and_emit("Generating improvement recommendations…")
    verdict = await _critic_pass(state, config=config, models=models)
    state.critic_verdicts.append(verdict)
    emit(
        "critic",
        round=len(state.critic_verdicts),
        total=verdict.total,
        verdict=verdict.verdict,
        feedback=list(verdict.feedback),
    )
    emit("recommendations", items=list(verdict.feedback), total=verdict.total)
    emit("stage_end", stage="critic")

    # 5. Rewrite the draft with citations woven in.
    if rewrite:
        emit("stage_start", stage="writer", label="Improving draft")
        log_and_emit("Rewriting the draft with citations…")
        agent = build_writer_agent(config, models)
        response = await _run_agent(agent, _improve_writer_inputs(state))
        state.draft = _text_of(response)
        state.iteration += 1
        emit("draft", markdown=state.draft or "", iteration=state.iteration)
        emit("stage_end", stage="writer")

    emit("done", final_verdict="improved")
    return state
