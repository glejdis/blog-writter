"""Bing grounding tool for the Research agent.

Real implementation uses Azure AI Foundry's Bing Grounding connection (Foundry
handles the actual Bing API call; we just attach the connection to the agent).
Falls back to a stub when no connection name is configured.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BingSearchTool:
    """Marker type describing a Bing grounding tool attached to a Foundry agent.

    The actual `HostedTool` / connection wiring happens in the agent factory
    (see `agents/research.py`) because Foundry's Bing grounding is configured
    per-agent via the project connection rather than as a free-standing
    callable.
    """

    connection_name: str

    @classmethod
    def from_env(cls) -> BingSearchTool | None:
        name = os.environ.get("BING_GROUNDING_CONNECTION_NAME")
        return cls(connection_name=name) if name else None


async def bing_search_stub(query: str) -> list[dict[str, str]]:
    """Stub used when no Bing connection is configured.

    Returns canned external sources so the smoke test can exercise the full
    pipeline without network access.
    """
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
