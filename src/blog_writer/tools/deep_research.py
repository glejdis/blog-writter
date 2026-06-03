"""Deep research via the Foundry ``o3-deep-research`` model.

This is the heavyweight, agentic alternative to :func:`blog_writer.tools.
bing_search.search_web`. It drives the Azure AI Agents **Deep Research tool**
(an ``o3-deep-research`` deployment grounded with Bing Search) which performs
multi-step web research and returns a synthesized, citation-rich report.

The Deep Research tool runs against a *separate* Foundry project (the
``o3-deep-research`` model is only available in a handful of regions, e.g.
westus / norwayeast), so it reads its own connection details from the
environment rather than the main ``foundry`` provider config:

* ``AZURE_AI_DEEP_RESEARCH_ENDPOINT``    — project endpoint
  (``https://<acct>.services.ai.azure.com/api/projects/<proj>``)
* ``AZURE_AI_DEEP_RESEARCH_MODEL``       — ``o3-deep-research`` deployment name
* ``AZURE_AI_DEEP_RESEARCH_AGENT_MODEL`` — orchestration chat deployment (e.g. ``gpt-4o``)
* ``AZURE_AI_BING_CONNECTION_ID``        — full resource id of the Bing grounding connection

Auth is Microsoft Entra ID via ``DefaultAzureCredential`` (no keys).

The underlying SDK (``azure-ai-agents``) is synchronous and runs can take
several minutes, so the public entry point :func:`deep_research` offloads the
work to a thread. On any misconfiguration or failure it returns
``("", [])`` so the caller can fall back to the lightweight search.
"""

from __future__ import annotations

import logging
import os

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


def _run_deep_research(query: str, max_citations: int, timeout_s: int) -> tuple[str, list[dict[str, str]]]:
    """Blocking deep-research run. Returns ``(report_markdown, citations)``."""
    cfg = _config_from_env()
    if cfg is None:
        return "", []

    try:
        from azure.ai.agents import AgentsClient
        from azure.ai.agents.models import (
            AgentThreadCreationOptions,
            DeepResearchTool,
            ListSortOrder,
            MessageRole,
            ThreadMessageOptions,
        )
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:  # pragma: no cover - defensive
        logger.warning("azure-ai-agents not installed: %s", exc)
        return "", []

    instructions = (
        "You are a meticulous technical research assistant. Research the topic "
        "thoroughly using web sources, prioritising official Microsoft Learn docs, "
        "Azure architecture guidance, and reputable engineering blogs. Produce a "
        "concise, well-structured briefing with concrete facts, version numbers, "
        "and trade-offs that a technical author can cite. Always ground claims in "
        "your cited sources.\n\n"
        "IMPORTANT: Do NOT ask the user any clarifying questions. Make reasonable "
        "assumptions, choose the most useful interpretation, and proceed directly "
        "to performing web research. Your final message MUST be the completed "
        "research briefing with inline source citations — never a list of questions."
    )

    task = (
        "Research the following topic and write the final briefing now. Do not ask "
        "clarifying questions; make reasonable assumptions and proceed.\n\n"
        f"Topic: {query}"
    )

    credential = DefaultAzureCredential()
    try:
        with AgentsClient(endpoint=cfg["endpoint"], credential=credential) as agents:
            dr_tool = DeepResearchTool(
                bing_grounding_connection_id=cfg["bing_conn"],
                deep_research_model=cfg["dr_model"],
            )
            agent = agents.create_agent(
                model=cfg["agent_model"],
                name="blog-deep-researcher",
                instructions=instructions,
                tools=dr_tool.definitions,
            )
            try:
                run = agents.create_thread_and_process_run(
                    agent_id=agent.id,
                    thread=AgentThreadCreationOptions(
                        messages=[ThreadMessageOptions(role=MessageRole.USER, content=task)]
                    ),
                    polling_interval=10,
                )
                if run.status != "completed":
                    logger.warning(
                        "Deep research run did not complete: status=%s error=%s",
                        run.status,
                        getattr(run, "last_error", None),
                    )
                    return "", []

                report, citations = "", []
                messages = agents.messages.list(
                    thread_id=run.thread_id, order=ListSortOrder.DESCENDING
                )
                for msg in messages:
                    if msg.role != MessageRole.AGENT:
                        continue
                    report = "\n\n".join(
                        t.text.value for t in getattr(msg, "text_messages", []) if t.text
                    ).strip()
                    for ann in getattr(msg, "url_citation_annotations", []) or []:
                        uc = ann.url_citation
                        if uc and uc.url:
                            citations.append(
                                {
                                    "title": uc.title or uc.url,
                                    "url": uc.url,
                                    "snippet": "",
                                    "type": "deep-research",
                                }
                            )
                    break  # most recent agent message is the final report

                # De-dupe citations by URL, preserving order.
                seen: set[str] = set()
                deduped = []
                for c in citations:
                    if c["url"] in seen:
                        continue
                    seen.add(c["url"])
                    deduped.append(c)
                return report, deduped[:max_citations]
            finally:
                try:
                    agents.delete_agent(agent.id)
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    logger.debug("Failed to delete deep-research agent", exc_info=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Deep research failed: %s", exc)
        return "", []
    finally:
        try:
            credential.close()
        except Exception:  # noqa: BLE001
            pass


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
