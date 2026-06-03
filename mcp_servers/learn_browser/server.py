"""FastMCP server exposing the Learn Browser helpers as MCP tools.

Run it standalone:

    python -m mcp_servers.learn_browser            # stdio (default)
    python -m mcp_servers.learn_browser --http     # HTTP on localhost:8765

Then point any MCP client at it — Claude Desktop, VS Code AI Toolkit,
Cursor, the blog-writer workflow itself — and call:

  * ``search_all_learn(query, top_k?)``       — broad search across all of learn.microsoft.com
  * ``search_curated_learn(query, top_k?)``   — scoped to CAF/WAF/Architecture Center/AI Foundry
  * ``fetch_learn_page(url)``                 — get any Learn page as markdown (cached 24h)
  * ``search_learn_code_samples(query, language?, top_k?)`` — Learn code snippets
  * ``search_github_azure_samples(query, top_k?)`` — public GitHub repo search in Azure orgs
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from blog_writer.config import load_config
from blog_writer.tools.learn_mcp import load_learn_scopes

from . import core

logger = logging.getLogger(__name__)


mcp = FastMCP(
    name="learn-browser",
    instructions=(
        "Custom MCP server that wraps the official Microsoft Learn Docs MCP "
        "and adds project-specific scoping + caching + Azure-Samples GitHub "
        "search. Prefer search_curated_learn for best-practice content (CAF / "
        "Well-Architected / Architecture Center / AI Foundry); use "
        "search_all_learn when you need broader product docs and tutorials."
    ),
)


@mcp.tool()
async def search_all_learn(query: str, top_k: int = 8) -> list[dict[str, Any]]:
    """Broad search across all of learn.microsoft.com — no allow-list filter.

    Returns up to ``top_k`` hits with ``{title, url, snippet, source}``.
    Use this for product docs, tutorials, quickstarts, and anything that
    falls outside the curated best-practice scope.
    """
    hits = await core.search_all_learn(query, top_k=top_k)
    return [h.to_dict() for h in hits]


@mcp.tool()
async def search_curated_learn(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Search Learn, post-filtered to the project's curated best-practice scope.

    The allow-list lives in ``knowledge_base/learn_scopes.yaml`` (CAF /
    Well-Architected / Architecture Center / AI Foundry). Use this when you
    specifically want authoritative best-practice content.
    """
    scope = load_learn_scopes(load_config())
    hits = await core.search_curated_learn(query, scope=scope, top_k=top_k)
    return [h.to_dict() for h in hits]


@mcp.tool()
async def fetch_learn_page(url: str) -> str:
    """Fetch a single learn.microsoft.com page as clean markdown (cached 24h).

    Returns an empty string if the upstream fetch fails so the caller can
    decide whether to retry.
    """
    body = await core.fetch_learn_page(url)
    return body or ""


@mcp.tool()
async def search_learn_code_samples(
    query: str, language: str | None = None, top_k: int = 5
) -> list[dict[str, Any]]:
    """Search Microsoft Learn for short code snippets.

    ``language`` is a soft hint (the upstream server uses it as a ranking
    signal, not a hard filter), so results may include other languages.
    Each hit has ``{description, code, url, language}``.
    """
    samples = await core.search_learn_code_samples(
        query, language=language, top_k=top_k
    )
    return [
        {
            "description": s.description,
            "code": s.code,
            "url": s.url,
            "language": s.language,
        }
        for s in samples
    ]


@mcp.tool()
async def search_github_azure_samples(
    query: str, top_k: int = 5
) -> list[dict[str, Any]]:
    """Search GitHub repos in the Azure-Samples / Azure / microsoft orgs.

    Uses the public REST search API. Set ``GITHUB_TOKEN`` in the environment
    to lift the unauthenticated rate limit (10 → 30 req/min). Returns
    ``[]`` on rate-limit or network failure.
    """
    hits = await core.search_github_azure_samples(query, top_k=top_k)
    return [h.to_dict() for h in hits]


def run_stdio() -> None:
    """Run the MCP server over stdio (the default for Claude Desktop etc)."""
    mcp.run()


def run_http(*, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the MCP server over HTTP (streamable).

    Useful when you want a long-lived server multiple clients can hit.
    """
    # FastMCP exposes ``settings`` for transport tuning.
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.run(transport="streamable-http")
