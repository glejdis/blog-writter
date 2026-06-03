"""External web search for the Research agent.

Strategy (whichever is configured wins; first match takes precedence):

1. **Tavily** (recommended for new setups) — set ``TAVILY_API_KEY``.
   Designed for agent / LLM workflows, generous free tier, returns clean
   excerpts with URLs.
2. **Bing Web Search v7** — set ``BING_SEARCH_API_KEY``. Being retired by
   Microsoft for new customers but still works for existing tenants.
3. **Foundry Bing Grounding** (placeholder) — set
   ``BING_GROUNDING_CONNECTION_NAME``. This requires an Azure AI Foundry
   project with a Bing connection. The framework doesn't currently expose
   a typed tool for this; the marker class below is kept so we can plug it
   in once ``agent_framework`` ships ``HostedWebSearchTool``.
4. **Stub** — used when nothing above is configured. Returns canned hits
   so the pipeline can still run end-to-end (used by the smoke test and
   for offline dev).

All four backends share the same return shape:
``list[dict[str, str]]`` with keys ``title``, ``url``, ``snippet``, ``type``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BingSearchTool:
    """Marker type describing a Foundry Bing grounding connection.

    The actual tool wiring will land here once ``agent_framework`` ships a
    typed ``HostedWebSearchTool``. For now this is consumed only as a flag
    by the workflow ("yes, the user has a Bing connection configured").
    """

    connection_name: str

    @classmethod
    def from_env(cls) -> BingSearchTool | None:
        name = os.environ.get("BING_GROUNDING_CONNECTION_NAME")
        return cls(connection_name=name) if name else None


# -----------------------------------------------------------------------------
# Real-mode search backends
# -----------------------------------------------------------------------------


async def _search_tavily(query: str, *, max_results: int = 5) -> list[dict[str, str]]:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx ships transitively with agent-framework
        logger.warning("httpx not installed; cannot call Tavily")
        return []
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search", json=payload, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - resilience over precision
        logger.warning("Tavily search failed: %s", exc)
        return []
    hits: list[dict[str, str]] = []
    for r in data.get("results") or []:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "")
        if not url:
            continue
        hits.append(
            {
                "title": str(r.get("title") or "Untitled"),
                "url": url,
                "snippet": str(r.get("content") or "")[:600],
                "type": "external",
            }
        )
    return hits


async def _search_bing_v7(query: str, *, max_results: int = 5) -> list[dict[str, str]]:
    api_key = os.environ.get("BING_SEARCH_API_KEY")
    if not api_key:
        return []
    try:
        import httpx
    except ImportError:  # pragma: no cover
        logger.warning("httpx not installed; cannot call Bing")
        return []
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    params = {"q": query, "count": max_results, "responseFilter": "Webpages"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                "https://api.bing.microsoft.com/v7.0/search",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bing v7 search failed: %s", exc)
        return []
    pages = (data.get("webPages") or {}).get("value") or []
    hits: list[dict[str, str]] = []
    for r in pages:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "")
        if not url:
            continue
        hits.append(
            {
                "title": str(r.get("name") or "Untitled"),
                "url": url,
                "snippet": str(r.get("snippet") or "")[:600],
                "type": "external",
            }
        )
    return hits


async def search_web(
    query: str,
    *,
    max_results: int = 5,
    timeout_s: int = 20,
) -> list[dict[str, str]]:
    """Run an external web search using whichever backend is configured.

    Tavily takes precedence over Bing v7 if both keys are present. Returns
    ``[]`` if neither is configured (the caller can fall back to
    ``bing_search_stub``).
    """
    _ = timeout_s  # reserved for per-backend override; each backend pins its own

    if os.environ.get("TAVILY_API_KEY"):
        try:
            return await asyncio.wait_for(
                _search_tavily(query, max_results=max_results), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            logger.warning("Tavily search timed out after %ss", timeout_s)
            return []
    if os.environ.get("BING_SEARCH_API_KEY"):
        try:
            return await asyncio.wait_for(
                _search_bing_v7(query, max_results=max_results), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            logger.warning("Bing v7 search timed out after %ss", timeout_s)
            return []
    return []


# -----------------------------------------------------------------------------
# Stub (offline / smoke test)
# -----------------------------------------------------------------------------


async def bing_search_stub(query: str) -> list[dict[str, str]]:
    """Canned external sources used in stub mode and as the offline fallback."""
    _ = query
    return [
        {
            "title": "Building agents with the Microsoft Agent Framework (devblogs)",
            "url": "https://devblogs.microsoft.com/example/building-agents-mc-agent-framework",
            "type": "blog",
            "snippet": (
                "Walkthrough of building a multi-agent system on Azure with the new "
                "Microsoft Agent Framework, including MCP tool integration."
            ),
        },
        {
            "title": "Azure-Samples/agent-mcp-quickstart",
            "url": "https://github.com/Azure-Samples/agent-mcp-quickstart",
            "type": "repo",
            "snippet": "Minimal example of an Agent Framework agent calling the MS Learn MCP server.",
        },
    ]
