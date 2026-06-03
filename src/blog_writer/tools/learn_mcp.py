"""Microsoft Learn Docs MCP integration.

The MS Learn MCP server (https://learn.microsoft.com/api/mcp) exposes three
tools:

- ``microsoft_docs_search(query)`` — ranked search across all of learn.microsoft.com.
- ``microsoft_docs_fetch(url)``   — fetch a single Learn page as markdown.
- ``microsoft_code_sample_search(query, language)`` — search code snippets,
  optionally filtered by language.

This module exposes three layers:

1. **Direct MCP helpers** (``search_learn``, ``fetch_learn_page``,
   ``search_learn_code_samples``) — use the ``mcp`` SDK directly, no LLM in
   the loop. The workflow's Internal Knowledge stage uses these so citations
   are always grounded in a real search result rather than depending on the
   LLM to remember to call the tool.

2. **Per-agent tool factories** (``build_learn_mcp_tool``,
   ``build_learn_code_sample_tool``) — return ``MCPStreamableHTTPTool``
   instances scoped to a specific subset of MCP tools so each agent only
   sees the tools relevant to its role.

3. **``LearnScopeFilter``** — post-filters search results against the
   curated allow-list in ``knowledge_base/learn_scopes.yaml`` so we keep
   only the high-signal "best practice" content (CAF / WAF / Architecture
   Center / AI Foundry).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re as _re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from blog_writer.config import AppConfig

logger = logging.getLogger(__name__)


DEFAULT_MS_LEARN_MCP_URL = "https://learn.microsoft.com/api/mcp"
DEFAULT_REQUEST_TIMEOUT_S = 30

# Locale segments look like "en-us", "de-de", "zh-cn", etc. — exactly two
# lowercase ASCII chunks separated by a hyphen.
_LOCALE_RE = _re.compile(r"^/[a-z]{2,3}-[a-z]{2,4}(?=/|$)")


def _strip_locale_prefix(path: str) -> str:
    return _LOCALE_RE.sub("", path) or "/"


# -----------------------------------------------------------------------------
# Data shapes
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class LearnHit:
    """A single ranked result from ``microsoft_docs_search``."""

    title: str
    url: str
    excerpt: str


@dataclass(frozen=True)
class LearnCodeSample:
    """A single ranked result from ``microsoft_code_sample_search``."""

    description: str
    code: str
    url: str
    language: str


@dataclass(frozen=True)
class LearnScopeFilter:
    """Allow-list of Learn URL path prefixes loaded from learn_scopes.yaml."""

    allow_list: tuple[str, ...]
    fallback_behavior: str = "report_empty"

    def is_in_scope(self, url: str) -> bool:
        # ``urlparse(url).path`` already drops the fragment (e.g. "#section").
        path = urlparse(url).path or ""
        # Learn URLs may be namespaced under a locale segment, e.g.
        # /en-us/azure/cloud-adoption-framework/... — strip it before matching.
        path = _strip_locale_prefix(path)
        return any(path.startswith(p) for p in self.allow_list)

    def filter_hits(self, hits: list[LearnHit]) -> list[LearnHit]:
        return [h for h in hits if self.is_in_scope(h.url)]

    def filter_code_samples(self, samples: list[LearnCodeSample]) -> list[LearnCodeSample]:
        return [s for s in samples if self.is_in_scope(s.url)]


def load_learn_scopes(config: AppConfig) -> LearnScopeFilter:
    """Read ``knowledge_base/learn_scopes.yaml`` and return a ``LearnScopeFilter``."""
    path = Path(config.knowledge_base_dir) / "learn_scopes.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Missing learn scopes file: {path}")
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    allow: list[str] = list(data.get("allow_list") or [])
    fallback: str = str(data.get("fallback_behavior") or "report_empty")
    if not allow:
        raise ValueError(f"learn_scopes.yaml has an empty allow_list: {path}")
    return LearnScopeFilter(allow_list=tuple(allow), fallback_behavior=fallback)


# -----------------------------------------------------------------------------
# Per-agent tool factories
# -----------------------------------------------------------------------------


# The MCP server exposes more tools than any single agent should use. We scope
# each agent's view with ``allowed_tools`` so the Internal Knowledge agent
# never picks up the code-sample tool, etc.
_INTERNAL_KNOWLEDGE_TOOLS: tuple[str, ...] = (
    "microsoft_docs_search",
    "microsoft_docs_fetch",
)
_POC_BUILDER_TOOLS: tuple[str, ...] = ("microsoft_code_sample_search",)


def build_learn_mcp_tool(
    *,
    url: str = DEFAULT_MS_LEARN_MCP_URL,
    name: str = "ms_learn_docs",
    description: str | None = None,
    allowed_tools: tuple[str, ...] | None = _INTERNAL_KNOWLEDGE_TOOLS,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT_S,
):
    """MCP tool for the Internal Knowledge agent.

    Defaults to exposing only ``microsoft_docs_search`` and
    ``microsoft_docs_fetch`` so the agent can't accidentally use the
    code-sample tool (that belongs to the PoC Builder).
    """
    from agent_framework import MCPStreamableHTTPTool

    return MCPStreamableHTTPTool(
        name=name,
        url=url,
        description=description
        or (
            "Search and fetch Microsoft Learn documentation. Use this for any Azure "
            "best-practice content, especially the Cloud Adoption Framework, "
            "Well-Architected Framework, Architecture Center, and AI Foundry docs."
        ),
        allowed_tools=allowed_tools,
        request_timeout=request_timeout,
    )


def build_learn_code_sample_tool(
    *,
    url: str = DEFAULT_MS_LEARN_MCP_URL,
    name: str = "ms_learn_code_samples",
    description: str | None = None,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT_S,
):
    """MCP tool for the PoC Builder.

    Exposes only ``microsoft_code_sample_search`` so the PoC Builder can
    ground a sample in real Microsoft Learn code snippets before generating
    its own.
    """
    from agent_framework import MCPStreamableHTTPTool

    return MCPStreamableHTTPTool(
        name=name,
        url=url,
        description=description
        or (
            "Search official Microsoft Learn for short code snippets in a given "
            "programming language. Use this BEFORE writing a PoC from scratch — "
            "if Microsoft already publishes a snippet for the API you need, "
            "start from it."
        ),
        allowed_tools=_POC_BUILDER_TOOLS,
        request_timeout=request_timeout,
    )


# -----------------------------------------------------------------------------
# Direct MCP helpers (no LLM in the loop)
# -----------------------------------------------------------------------------


async def _call_mcp_tool_raw(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    url: str = DEFAULT_MS_LEARN_MCP_URL,
    timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
) -> str | None:
    """Open a fresh MCP session, call one tool, return the raw text payload.

    The Learn MCP server returns a single text content block per call. Whether
    its body is JSON or plain markdown depends on the tool (search → JSON,
    fetch → markdown), so this helper returns the raw string and callers
    parse it as they see fit.

    Returns ``None`` on any network / protocol failure (logged); callers
    should treat that as "no results found" rather than raising.
    """
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError:  # pragma: no cover - mcp is a hard dep
        logger.warning("mcp SDK not installed; cannot call %s", tool_name)
        return None

    async def _run() -> str | None:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(tool_name, arguments)
                if res.isError:
                    logger.warning("MCP %s returned error: %s", tool_name, res.content)
                    return None
                if not res.content:
                    return None
                blob = getattr(res.content[0], "text", None)
                return blob if isinstance(blob, str) and blob else None

    try:
        return await asyncio.wait_for(_run(), timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.warning("MCP %s timed out after %ss", tool_name, timeout_s)
        return None
    except Exception as exc:  # noqa: BLE001 - resilience over precision
        logger.warning("MCP %s failed: %s", tool_name, exc)
        return None


async def _call_mcp_tool_json(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    url: str = DEFAULT_MS_LEARN_MCP_URL,
    timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
) -> dict[str, Any] | None:
    """Same as ``_call_mcp_tool_raw`` but parses the response as JSON.

    Returns ``None`` on any failure (network, non-JSON body, etc.).
    """
    blob = await _call_mcp_tool_raw(
        tool_name, arguments, url=url, timeout_s=timeout_s
    )
    if not blob:
        return None
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError as exc:
        logger.warning("MCP %s returned non-JSON payload: %s", tool_name, exc)
        return None
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"results": parsed}
    return None


async def search_learn(
    query: str,
    *,
    url: str = DEFAULT_MS_LEARN_MCP_URL,
    timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
) -> list[LearnHit]:
    """Call ``microsoft_docs_search`` and return parsed ``LearnHit`` objects.

    On failure returns ``[]`` and logs a warning — the workflow then falls
    back to whatever the Research agent finds.
    """
    data = await _call_mcp_tool_json(
        "microsoft_docs_search",
        {"query": query},
        url=url,
        timeout_s=timeout_s,
    )
    if not data:
        return []
    hits: list[LearnHit] = []
    for r in data.get("results") or []:
        if not isinstance(r, dict):
            continue
        u = r.get("contentUrl") or r.get("url") or ""
        if not u:
            continue
        hits.append(
            LearnHit(
                title=str(r.get("title") or "Untitled"),
                url=str(u),
                excerpt=str(r.get("content") or r.get("snippet") or "")[:1200],
            )
        )
    return hits


async def fetch_learn_page(
    page_url: str,
    *,
    url: str = DEFAULT_MS_LEARN_MCP_URL,
    timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
) -> str | None:
    """Fetch a single Learn page via ``microsoft_docs_fetch``. Returns markdown.

    The MCP server returns the page body as raw markdown (not JSON), so we
    take the text content as-is.
    """
    return await _call_mcp_tool_raw(
        "microsoft_docs_fetch",
        {"url": page_url},
        url=url,
        timeout_s=timeout_s,
    )


async def search_learn_code_samples(
    query: str,
    *,
    language: str = "python",
    url: str = DEFAULT_MS_LEARN_MCP_URL,
    timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
) -> list[LearnCodeSample]:
    """Call ``microsoft_code_sample_search`` and return parsed samples.

    Note: the MCP server treats ``language`` as a soft hint, not a hard
    filter — results may include other languages. Callers that need a
    strict filter should post-filter the returned list themselves.
    """
    data = await _call_mcp_tool_json(
        "microsoft_code_sample_search",
        {"query": query, "language": language},
        url=url,
        timeout_s=timeout_s,
    )
    if not data:
        return []
    samples: list[LearnCodeSample] = []
    for r in data.get("results") or []:
        if not isinstance(r, dict):
            continue
        link = r.get("link") or r.get("contentUrl") or r.get("url") or ""
        code = r.get("codeSnippet") or r.get("code") or ""
        if not code:
            continue
        samples.append(
            LearnCodeSample(
                description=str(r.get("description") or "")[:600],
                code=str(code),
                url=str(link),
                language=str(r.get("language") or language),
            )
        )
    return samples


# -----------------------------------------------------------------------------
# Stub data — used when no MCP server is reachable (offline tests, CI).
# -----------------------------------------------------------------------------


_STUB_HITS: tuple[LearnHit, ...] = (
    LearnHit(
        title="Cloud Adoption Framework: Adopt AI responsibly",
        url="https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/scenarios/ai/",
        excerpt=(
            "The Microsoft Cloud Adoption Framework for Azure offers guidance and best "
            "practices to responsibly adopt AI."
        ),
    ),
    LearnHit(
        title="Azure Well-Architected Framework: AI workloads",
        url="https://learn.microsoft.com/en-us/azure/well-architected/ai/",
        excerpt=(
            "Use the five pillars to design and operate AI workloads on Azure that meet "
            "reliability, security, cost, operational, and performance goals."
        ),
    ),
    LearnHit(
        title="Azure AI Foundry Agent Service overview",
        url="https://learn.microsoft.com/en-us/azure/ai-foundry/agents/overview",
        excerpt=(
            "The Azure AI Foundry Agent Service is a managed runtime for building, "
            "deploying, and operating AI agents at scale."
        ),
    ),
)


async def search_learn_stub(query: str) -> list[LearnHit]:
    """Stub implementation — returns canned in-scope hits regardless of query."""
    _ = query
    return list(_STUB_HITS)
