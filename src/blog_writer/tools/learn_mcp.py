"""Microsoft Learn Docs MCP integration.

Two pieces:

1. **`build_learn_mcp_tool`** — returns an `MCPStreamableHTTPTool` connected to
   `https://learn.microsoft.com/api/mcp`, ready to attach to an Agent. This is
   how the Internal Knowledge agent actually queries Microsoft Learn.

2. **`LearnScopeFilter`** — post-filters MCP results against the curated
   allow-list in `knowledge_base/learn_scopes.yaml`. We don't try to scope at
   query time (the MCP server doesn't expose a path filter); we filter the
   results after the agent gets them.

The agent's system prompt instructs it to drop out-of-scope hits itself, but
we also expose the filter as a callable tool so the workflow code can apply
it deterministically post-hoc.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from blog_writer.config import AppConfig


DEFAULT_MS_LEARN_MCP_URL = "https://learn.microsoft.com/api/mcp"

# Locale segments look like "en-us", "de-de", "zh-cn", etc. — exactly two
# lowercase ASCII chunks separated by a hyphen.
import re as _re  # noqa: E402

_LOCALE_RE = _re.compile(r"^/[a-z]{2,3}-[a-z]{2,4}(?=/|$)")


def _strip_locale_prefix(path: str) -> str:
    return _LOCALE_RE.sub("", path) or "/"


@dataclass(frozen=True)
class LearnHit:
    """A single ranked result from `microsoft_docs_search`, after scope filtering."""

    title: str
    url: str
    excerpt: str


@dataclass(frozen=True)
class LearnScopeFilter:
    """Allow-list of Learn URL path prefixes loaded from learn_scopes.yaml."""

    allow_list: tuple[str, ...]
    fallback_behavior: str = "report_empty"

    def is_in_scope(self, url: str) -> bool:
        path = urlparse(url).path or ""
        # Learn URLs are namespaced under a locale segment, e.g.
        # /en-us/azure/cloud-adoption-framework/... — strip it before matching.
        path = _strip_locale_prefix(path)
        return any(path.startswith(p) for p in self.allow_list)

    def filter_hits(self, hits: list[LearnHit]) -> list[LearnHit]:
        return [h for h in hits if self.is_in_scope(h.url)]


def load_learn_scopes(config: AppConfig) -> LearnScopeFilter:
    """Read `knowledge_base/learn_scopes.yaml` and return a `LearnScopeFilter`."""
    path = Path(config.knowledge_base_dir) / "learn_scopes.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Missing learn scopes file: {path}")
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    allow: list[str] = list(data.get("allow_list") or [])
    fallback: str = str(data.get("fallback_behavior") or "report_empty")
    if not allow:
        raise ValueError(f"learn_scopes.yaml has an empty allow_list: {path}")
    return LearnScopeFilter(allow_list=tuple(allow), fallback_behavior=fallback)


def build_learn_mcp_tool(
    *,
    url: str = DEFAULT_MS_LEARN_MCP_URL,
    name: str = "ms_learn_docs",
    description: str | None = None,
):
    """Build a Microsoft Agent Framework MCP tool pointing at the MS Learn MCP server.

    Returns an `MCPStreamableHTTPTool` instance ready to pass into
    `Agent(... tools=[learn_tool])`.

    Lazily imports the framework so this module stays importable in stub mode
    without paying the cost.
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
    )


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
    """Stub implementation — returns canned in-scope hits regardless of query.

    Used by the smoke test and stub-mode runs.
    """
    _ = query
    return list(_STUB_HITS)
