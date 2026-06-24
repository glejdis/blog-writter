"""Deep research via the Foundry ``o3-deep-research`` model.

This is the heavyweight alternative to :func:`blog_writer.tools.bing_search.
search_web`. It drives the ``o3-deep-research`` model with the **web search
tool** on the Foundry **Responses API** — the supported successor to the (now
deprecated) Azure AI Agents Deep Research tool — to perform multi-step,
Bing-grounded web research and return a synthesized, citation-rich report.

Deep research runs against a *separate* Foundry project (the
``o3-deep-research`` model is only available in a handful of regions, e.g.
westus / norwayeast), so it reads its own connection details from the
environment rather than the main ``foundry`` provider config:

* ``AZURE_AI_DEEP_RESEARCH_ENDPOINT``    — project endpoint
  (``https://<acct>.services.ai.azure.com/api/projects/<proj>``)
* ``AZURE_AI_DEEP_RESEARCH_MODEL``       — ``o3-deep-research`` deployment name
* ``AZURE_AI_BING_CONNECTION_ID``        — the project's Bing grounding connection
  (the web-search tool resolves it automatically from the project; required
  here only so we know a Grounding-with-Bing connection exists)

Auth is Microsoft Entra ID via ``DefaultAzureCredential`` (no keys), scoped to
``https://ai.azure.com/.default``.

A deep-research pass can take several minutes, so the run is dispatched in
``background`` mode and polled, and the public entry point :func:`deep_research`
offloads the blocking work to a thread. On any misconfiguration or failure it
returns ``("", [])`` so the caller can fall back to the lightweight search.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# How long to wait for a single deep-research run before giving up.
_DEFAULT_TIMEOUT_S = 900


def _config_from_env() -> dict[str, str] | None:
    """Return the deep-research connection settings, or ``None`` if incomplete."""
    endpoint = os.environ.get("AZURE_AI_DEEP_RESEARCH_ENDPOINT")
    dr_model = os.environ.get("AZURE_AI_DEEP_RESEARCH_MODEL")
    agent_model = os.environ.get("AZURE_AI_DEEP_RESEARCH_AGENT_MODEL", "gpt-4o")
    bing_conn = os.environ.get("AZURE_AI_BING_CONNECTION_ID")
    missing = [
        name
        for name, value in (
            ("AZURE_AI_DEEP_RESEARCH_ENDPOINT", endpoint),
            ("AZURE_AI_DEEP_RESEARCH_MODEL", dr_model),
            ("AZURE_AI_BING_CONNECTION_ID", bing_conn),
        )
        if not value
    ]
    if missing:
        logger.warning("Deep research disabled — missing env: %s", ", ".join(missing))
        return None
    return {
        "endpoint": endpoint,  # type: ignore[dict-item]
        "dr_model": dr_model,  # type: ignore[dict-item]
        "agent_model": agent_model,
        "bing_conn": bing_conn,  # type: ignore[dict-item]
    }


_REQUIRED_DEEP_RESEARCH_VARS = (
    "AZURE_AI_DEEP_RESEARCH_ENDPOINT",
    "AZURE_AI_DEEP_RESEARCH_MODEL",
    "AZURE_AI_BING_CONNECTION_ID",
)


def missing_deep_research_vars() -> list[str]:
    """Return the required deep-research env vars that are currently unset."""
    return [name for name in _REQUIRED_DEEP_RESEARCH_VARS if not os.environ.get(name)]


def deep_research_available() -> bool:
    """Return ``True`` when all deep-research connection env vars are present.

    Lets callers tell whether a requested deep-research run will actually invoke
    the ``o3-deep-research`` model or silently fall back to lightweight search,
    so they can surface that to the user instead of degrading silently.
    """
    return not missing_deep_research_vars()


def _run_deep_research(query: str, max_citations: int, timeout_s: int) -> tuple[str, list[dict[str, str]]]:
    """Blocking deep-research run via the Responses API. Returns ``(report, citations)``.

    Uses the ``o3-deep-research`` model with the web-search tool on the Foundry
    Responses API (the supported successor to the deprecated Agents Deep
    Research tool). The run is dispatched in ``background`` mode and polled to
    completion, since a deep-research pass can take several minutes.
    """
    cfg = _config_from_env()
    if cfg is None:
        return "", []

    try:
        from azure.identity import DefaultAzureCredential
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - defensive
        logger.warning("openai/azure-identity not installed: %s", exc)
        return "", []

    instructions = (
        "You are a meticulous technical research assistant. Research the topic "
        "thoroughly with web search, prioritising official Microsoft Learn docs, "
        "Azure architecture guidance, and reputable engineering blogs. Produce a "
        "concise, well-structured briefing with concrete facts, version numbers, "
        "and trade-offs that a technical author can cite, grounding every claim in "
        "an inline source citation. Do NOT ask clarifying questions — make "
        "reasonable assumptions and write the completed briefing directly."
    )

    base_url = cfg["endpoint"].rstrip("/") + "/openai/v1/"
    # Codes worth retrying with backoff (e.g. per-minute rate limits, transient
    # server hiccups) rather than failing straight to the lightweight fallback.
    transient_codes = {"rate_limit_exceeded", "server_error", "tool_server_error", "timeout"}
    max_attempts = 3
    backoff_s = 45
    deadline = time.monotonic() + timeout_s
    try:
        credential = DefaultAzureCredential()
        # A token taken now comfortably outlives a multi-minute research run.
        token = credential.get_token("https://ai.azure.com/.default").token
        client = OpenAI(base_url=base_url, api_key=token)

        response = None
        for attempt in range(1, max_attempts + 1):
            response = client.responses.create(
                model=cfg["dr_model"],
                instructions=instructions,
                input=query,
                tools=[{"type": "web_search_preview"}],
                background=True,
            )
            while response.status in ("queued", "in_progress"):
                if time.monotonic() > deadline:
                    logger.warning(
                        "Deep research timed out after %ss (status=%s)", timeout_s, response.status
                    )
                    try:
                        client.responses.cancel(response.id)
                    except Exception:  # noqa: BLE001 - best-effort
                        pass
                    return "", []
                time.sleep(5)
                response = client.responses.retrieve(response.id)

            if response.status == "completed":
                break

            err = getattr(response, "error", None)
            code = getattr(err, "code", None)
            if code in transient_codes and attempt < max_attempts and time.monotonic() + backoff_s < deadline:
                logger.warning(
                    "Deep research attempt %d/%d failed transiently (%s); retrying in %ss",
                    attempt,
                    max_attempts,
                    code,
                    backoff_s,
                )
                time.sleep(backoff_s)
                continue
            logger.warning(
                "Deep research did not complete: status=%s error=%s", response.status, err
            )
            return "", []

        if response is None or response.status != "completed":
            return "", []

        report = (getattr(response, "output_text", "") or "").strip()

        citations: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in getattr(response, "output", None) or []:
            if getattr(item, "type", None) != "message":
                continue
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) != "output_text":
                    continue
                for ann in getattr(part, "annotations", None) or []:
                    if getattr(ann, "type", None) != "url_citation":
                        continue
                    url = getattr(ann, "url", None)
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    citations.append(
                        {
                            "title": getattr(ann, "title", None) or url,
                            "url": url,
                            "snippet": "",
                            "type": "deep-research",
                        }
                    )
        return report, citations[:max_citations]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Deep research failed: %s", exc)
        return "", []


async def deep_research(
    query: str, *, max_citations: int = 8, timeout_s: int = _DEFAULT_TIMEOUT_S
) -> tuple[str, list[dict[str, str]]]:
    """Run an agentic deep-research pass for ``query``.

    Returns ``(report_markdown, citations)`` where ``citations`` matches the
    ``[{title, url, snippet, type}]`` shape used by the rest of the pipeline.
    Returns ``("", [])`` if deep research is not configured or the run fails,
    so callers can fall back to the lightweight search.
    """
    import asyncio

    return await asyncio.to_thread(_run_deep_research, query, max_citations, timeout_s)
