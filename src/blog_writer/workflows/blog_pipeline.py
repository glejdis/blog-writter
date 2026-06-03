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

from blog_writer.agents import (
    build_critic_agent,
    build_fact_checker_agent,
    build_ideation_agent,
    build_internal_knowledge_agent,
    build_orchestrator_agent,
    build_planner_agent,
    build_poc_builder_agent,
    build_research_agent,
    build_writer_agent,
)
from blog_writer.config import AppConfig
from blog_writer.models import ModelMap, load_model_map
from blog_writer.tools.bing_search import bing_search_stub, search_web
from blog_writer.tools.code_sandbox import run_in_sandbox
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


async def run_blog_pipeline(
    seed: str,
    *,
    config: AppConfig,
    models: ModelMap | None = None,
    on_human_input: HumanCallback | None = None,
    autonomous: bool = False,
    progress: Callable[[str], None] | None = None,
) -> BlogState:
    """Run the full pipeline and return the final state."""
    models = models or load_model_map()
    state = BlogState(seed=seed)
    _log = progress or (lambda _msg: None)

    # 1. Ideation
    _log("Generating angles…")
    state = await _ideate(state, config=config, models=models)
    _log(f"Generated {len(state.angles)} angles.")

    # 2. Human checkpoint: pick angle
    state.angle = await _pick_angle(
        state,
        on_human_input=on_human_input,
        autonomous=autonomous,
    )
    _log(f"Angle: {state.angle}")

    # 3. Internal knowledge + external research (sequential here; trivially
    # parallelisable with asyncio.gather once we want to spend the API budget).
    _log("Gathering internal best practices (MS Learn)…")
    state = await _internal_knowledge(state, config=config, models=models)
    _log(f"Got {len(state.internal_hits)} in-scope Learn hits.")

    _log("Gathering external research…")
    state = await _external_research(state, config=config, models=models)
    _log(f"Got {len(state.external_hits)} external hits.")

    # 4. Plan
    _log("Drafting outline…")
    state = await _plan(state, config=config, models=models)

    # 5. Human checkpoint: approve plan
    state.plan_approved = await _approve_plan(
        state,
        on_human_input=on_human_input,
        autonomous=autonomous,
    )
    if not state.plan_approved:
        _log("Plan not approved. Aborting.")
        return state

    # 6. PoCs
    _log(f"Building {len(state.outline.pocs) if state.outline else 0} PoC(s)…")
    state = await _build_pocs(state, config=config, models=models)

    # 7. Writer ⇄ Critic revision loop
    _log("Writing draft…")
    state = await _write_draft(state, config=config, models=models)
    state = await _fact_check(state, config=config, models=models)
    state = await _critic_loop(state, config=config, models=models, log=_log)

    # 8. Final orchestrator review
    _log("Final review…")
    state = await _final_review(state, config=config, models=models)
    return state


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
    response = await _run_agent(
        agent,
        _planner_inputs(state),
    )
    text = _text_of(response)
    state.outline = _parse_outline(text, fallback_title=state.angle or state.seed)
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
) -> BlogState:
    for revision in range(config.max_revisions + 1):
        verdict = await _critic_pass(state, config=config, models=models)
        state.critic_verdicts.append(verdict)
        log(f"Critic round {revision + 1}: total={verdict.total} -> {verdict.verdict}")
        if verdict.verdict == "accept" or verdict.total >= config.critic_threshold:
            return state
        if revision >= config.max_revisions:
            log(
                f"Hit max revisions ({config.max_revisions}); accepting current draft."
            )
            return state
        log("Revising draft…")
        state = await _write_draft(state, config=config, models=models)
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


def _planner_inputs(state: BlogState) -> str:
    return (
        f"Angle: {state.angle}\n\n"
        f"Internal best practices ({len(state.internal_hits)} hits):\n"
        + _citations_yaml(state, only="learn")
        + f"\n\nExternal research ({len(state.external_hits)} hits):\n"
        + _citations_yaml(state, only="external")
        + "\n\nReturn the YAML outline + PoC list as specified."
    )


def _writer_inputs(state: BlogState) -> str:
    pocs_summary = "\n".join(
        f"- {r.spec.id} ({r.spec.section}): exit={r.exit_code}, "
        f"stdout_snippet={r.stdout[:120]!r}"
        for r in state.poc_results
    )
    return (
        f"Outline:\n{_outline_summary(state)}\n\n"
        f"Citations (use Learn first):\n{_citations_yaml(state)}\n\n"
        f"PoC results:\n{pocs_summary}\n\n"
        "Write the full Markdown draft now."
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


def _parse_outline(text: str, *, fallback_title: str) -> Outline:
    """Parse the Planner's YAML output into an `Outline`.

    Falls back to a one-section, no-PoC outline if parsing fails — the pipeline
    must keep moving even when the model returns malformed YAML.
    """
    try:
        import yaml

        data = yaml.safe_load(text)
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
