"""Learn Browser — a custom MCP server that reads every Microsoft Learn page.

Wraps the official Microsoft Learn Docs MCP server
(``https://learn.microsoft.com/api/mcp``, no auth) and adds project-specific
value:

* **Broad search** across all of learn.microsoft.com (no curated allow-list)
  for the Research / "fill the gaps" stage of the blog-writer pipeline.
* **Curated-scope search** that post-filters hits against the project's
  ``knowledge_base/learn_scopes.yaml`` (CAF / WAF / Architecture Center /
  AI Foundry) for the Internal Knowledge stage.
* **On-disk caching** so repeated fetches of the same page don't re-hit the
  upstream server.
* **Azure code-sample search** via the official ``microsoft_code_sample_search``
  tool plus an optional GitHub fallback (``Azure-Samples`` / ``Azure`` orgs).

Public API:

* ``mcp_servers.learn_browser.core`` — async Python helpers (use these from
  in-process code like the workflow).
* ``mcp_servers.learn_browser.server`` — FastMCP server exposing the same
  helpers as MCP tools (use this when attaching the server to an external
  MCP client).
"""

from .core import (
    fetch_learn_page,
    search_all_learn,
    search_curated_learn,
    search_github_azure_samples,
    search_learn_code_samples,
)

__all__ = [
    "fetch_learn_page",
    "search_all_learn",
    "search_curated_learn",
    "search_learn_code_samples",
    "search_github_azure_samples",
]
