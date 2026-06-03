"""External web search for the Research agent.

Backed by the project's **custom Learn Browser MCP server**
(``mcp_servers/learn_browser/``) which wraps the official Microsoft Learn
Docs MCP and optionally augments with public GitHub samples. No third-party
API keys required.

The Foundry Bing-grounding-via-connection marker class is kept as a
placeholder for the day ``agent-framework`` ships a typed
``HostedWebSearchTool`` (until then it's unused — set
``BING_GROUNDING_CONNECTION_NAME`` only if you want to track that you have a
Foundry connection configured).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BingSearchTool:
    """Marker type for a future Foundry Bing-grounding connection."""

    connection_name: str

    @classmethod
    def from_env(cls) -> BingSearchTool | None:
        name = os.environ.get("BING_GROUNDING_CONNECTION_NAME")
        return cls(connection_name=name) if name else None


# -----------------------------------------------------------------------------
# Real search via the custom Learn Browser MCP server
# -----------------------------------------------------------------------------


async def search_web(
    query: str,
    *,
    learn_top_k: int = 5,
    github_top_k: int = 3,
    include_github: bool = True,
) -> list[dict[str, str]]:
    """Run the Research-stage external search via the custom MCP server.

    Pulls broad Microsoft Learn hits (no allow-list — that's what makes them
    "external" vs. the curated Internal Knowledge stage) and optionally
    augments with public GitHub repos from the Azure-Samples / Azure /
    microsoft orgs. Returns the same ``[{title, url, snippet, type}]`` shape
    as the legacy stub so the workflow can stay agnostic.

    Returns ``[]`` on failure; the caller should fall back to
    :func:`bing_search_stub`.
    """
    try:
        from mcp_servers.learn_browser.core import search_for_research
    except ImportError as exc:  # pragma: no cover - defensive
        logger.warning("Learn Browser MCP unavailable: %s", exc)
        return []

    try:
        raw = await search_for_research(
            query,
            learn_top_k=learn_top_k,
            github_top_k=github_top_k,
            include_github=include_github,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Learn Browser search failed: %s", exc)
        return []

    return [
        {
            "title": h.get("title", "Untitled"),
            "url": h.get("url", ""),
            "snippet": h.get("snippet", ""),
            "type": h.get("source", "external"),
        }
        for h in raw
        if h.get("url")
    ]


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
