"""Core async helpers for the Learn Browser MCP server.

These helpers are the source of truth — both the in-process workflow and the
standalone MCP server (``server.py``) call them. They wrap the official
Microsoft Learn Docs MCP server and add caching + project-specific scoping.

No third-party API keys are required: every backend either uses the public
Microsoft Learn MCP endpoint (no auth) or an anonymous public GitHub search
(rate-limited but adequate for blog research).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blog_writer.tools.learn_mcp import (
    DEFAULT_MS_LEARN_MCP_URL,
    DEFAULT_REQUEST_TIMEOUT_S,
    LearnCodeSample,
    LearnHit,
    LearnScopeFilter,
    fetch_learn_page as _upstream_fetch_learn_page,
    search_learn as _upstream_search_learn,
    search_learn_code_samples as _upstream_search_learn_code_samples,
)

from .cache import TTLCache


logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Cache locations (overridable via env so the FastMCP server can use its own)
# -----------------------------------------------------------------------------

_DEFAULT_CACHE_ROOT = Path(
    os.environ.get(
        "LEARN_BROWSER_CACHE_DIR",
        str(Path.home() / ".cache" / "blog-writer" / "learn_browser"),
    )
)

_fetch_cache = TTLCache(_DEFAULT_CACHE_ROOT / "fetch")
_search_cache = TTLCache(
    _DEFAULT_CACHE_ROOT / "search",
    default_ttl_s=6 * 60 * 60,  # search results go stale faster than page bodies
)


@dataclass(frozen=True)
class WebHit:
    """A normalized external/research hit (Learn-broad or GitHub)."""

    title: str
    url: str
    snippet: str
    source: str  # "learn" | "github"

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
        }


# -----------------------------------------------------------------------------
# Search: broad MS Learn (no allow-list)
# -----------------------------------------------------------------------------


async def search_all_learn(
    query: str,
    *,
    top_k: int = 8,
    mcp_url: str = DEFAULT_MS_LEARN_MCP_URL,
    timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
) -> list[WebHit]:
    """Broad search across all of learn.microsoft.com — no allow-list filter.

    Use this for the Research / "fill the gaps" stage where you want product
    docs, tutorials, quickstarts, etc. that fall outside the curated
    best-practice scope used by Internal Knowledge.
    """
    cache_key = f"all|{mcp_url}|{query.lower().strip()}|{top_k}"
    cached = _search_cache.get(cache_key)
    if cached is not None:
        return [WebHit(**h) for h in cached]

    learn_hits = await _upstream_search_learn(query, url=mcp_url, timeout_s=timeout_s)
    hits = [_hit_from_learn(h) for h in learn_hits[:top_k]]
    _search_cache.set(cache_key, [h.to_dict() for h in hits])
    return hits


async def search_curated_learn(
    query: str,
    *,
    scope: LearnScopeFilter,
    top_k: int = 5,
    mcp_url: str = DEFAULT_MS_LEARN_MCP_URL,
    timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
) -> list[WebHit]:
    """Same as ``search_all_learn`` but post-filtered to the curated allow-list.

    This is what the Internal Knowledge agent should call: only CAF / WAF /
    Architecture Center / AI Foundry hits make it through.
    """
    learn_hits = await _upstream_search_learn(query, url=mcp_url, timeout_s=timeout_s)
    scoped = scope.filter_hits(learn_hits)[:top_k]
    return [_hit_from_learn(h) for h in scoped]


def _hit_from_learn(h: LearnHit) -> WebHit:
    return WebHit(title=h.title, url=h.url, snippet=h.excerpt, source="learn")


# -----------------------------------------------------------------------------
# Fetch a single Learn page (cached)
# -----------------------------------------------------------------------------


async def fetch_learn_page(
    page_url: str,
    *,
    mcp_url: str = DEFAULT_MS_LEARN_MCP_URL,
    timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
    use_cache: bool = True,
) -> str | None:
    """Fetch a single learn.microsoft.com page as markdown. Cached for 24h."""
    if use_cache:
        cached = _fetch_cache.get(page_url)
        if isinstance(cached, str):
            return cached

    body = await _upstream_fetch_learn_page(
        page_url, url=mcp_url, timeout_s=timeout_s
    )
    if body:
        _fetch_cache.set(page_url, body)
    return body


# -----------------------------------------------------------------------------
# Code samples
# -----------------------------------------------------------------------------


async def search_learn_code_samples(
    query: str,
    *,
    language: str | None = None,
    top_k: int = 5,
    mcp_url: str = DEFAULT_MS_LEARN_MCP_URL,
    timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
) -> list[LearnCodeSample]:
    """Search Microsoft Learn for short code snippets, optionally by language.

    Pass-through to the upstream tool with a top-k cap. ``language`` is a
    soft hint (the upstream server treats it as a ranking signal, not a hard
    filter), so callers should still inspect ``LearnCodeSample.language``
    and skip mismatches if strict typing matters.
    """
    samples = await _upstream_search_learn_code_samples(
        query, language=language, url=mcp_url, timeout_s=timeout_s
    )
    return samples[:top_k]


# -----------------------------------------------------------------------------
# Optional: GitHub Azure-Samples search (public, no auth required)
# -----------------------------------------------------------------------------


async def search_github_azure_samples(
    query: str,
    *,
    top_k: int = 5,
    orgs: tuple[str, ...] = ("Azure-Samples", "Azure", "microsoft"),
    timeout_s: int = 15,
) -> list[WebHit]:
    """Search GitHub for repos in Azure-Samples / Azure / microsoft orgs.

    Uses the public REST search API. No token required for low rates
    (10 req/min unauth, 30 req/min with ``GITHUB_TOKEN`` set). On failure
    returns ``[]`` — the caller decides whether to fall back.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover
        logger.warning("httpx not installed; cannot search GitHub")
        return []

    org_filter = " ".join(f"org:{o}" for o in orgs)
    full_query = f"{query} {org_filter}"
    params = {"q": full_query, "per_page": top_k, "sort": "stars", "order": "desc"}
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    cache_key = f"github|{full_query}|{top_k}"
    cached = _search_cache.get(cache_key)
    if cached is not None:
        return [WebHit(**h) for h in cached]

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - resilience over precision
        logger.warning("GitHub repo search failed: %s", exc)
        return []

    hits: list[WebHit] = []
    for r in data.get("items") or []:
        if not isinstance(r, dict):
            continue
        url = str(r.get("html_url") or "")
        if not url:
            continue
        hits.append(
            WebHit(
                title=str(r.get("full_name") or "Untitled"),
                url=url,
                snippet=str(r.get("description") or "")[:500],
                source="github",
            )
        )
    _search_cache.set(cache_key, [h.to_dict() for h in hits])
    return hits


# -----------------------------------------------------------------------------
# Convenience: combined research search
# -----------------------------------------------------------------------------


async def search_for_research(
    query: str,
    *,
    learn_top_k: int = 5,
    github_top_k: int = 3,
    include_github: bool = True,
    mcp_url: str = DEFAULT_MS_LEARN_MCP_URL,
) -> list[dict[str, Any]]:
    """One-shot helper for the Research agent.

    Pulls broad MS Learn results plus (optionally) Azure-Samples repos. Returns
    a plain ``list[dict]`` ready to be turned into ``Citation`` objects by
    the workflow. ``include_github`` defaults on; flip it off (or unset
    ``GITHUB_TOKEN``) if you keep hitting rate limits.
    """
    learn_hits = await search_all_learn(query, top_k=learn_top_k, mcp_url=mcp_url)
    out = [h.to_dict() for h in learn_hits]
    if include_github:
        gh_hits = await search_github_azure_samples(query, top_k=github_top_k)
        out.extend(h.to_dict() for h in gh_hits)
    return out
