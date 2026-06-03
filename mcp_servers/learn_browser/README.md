# Learn Browser MCP Server

A custom Model Context Protocol server that turns the **entire** Microsoft
Learn catalog into a tool any MCP client can call. Built for this project's
Research / "fill the gaps" agent, but standalone-usable from Claude Desktop,
VS Code AI Toolkit, Cursor, or any other MCP client.

## Why this exists

The blog-writer pipeline already uses the official
[`learn.microsoft.com/api/mcp`](https://learn.microsoft.com/api/mcp) server
for the *Internal Knowledge* stage, but post-filtered to a curated allow-list
of "best practice" content (CAF, Well-Architected, Architecture Center,
AI Foundry). For the *Research* stage we want the opposite: **broad coverage**
across all of learn.microsoft.com to pick up product docs, tutorials, and
quickstarts that round out the post.

Rather than reach for a third-party search API (Tavily / Bing / etc.), this
server wraps the official Microsoft endpoint and exposes:

* A **broad** search path (no allow-list)
* A **curated** search path (same allow-list the workflow uses internally)
* A cached single-page fetcher
* The official code-sample search
* An optional public-GitHub search scoped to the Azure-Samples / Azure /
  microsoft orgs

All backends are free and **no API keys are required**.

## Tools exposed

| Tool                          | Purpose                                                                   |
|-------------------------------|---------------------------------------------------------------------------|
| `search_all_learn`            | Broad MS Learn search (no allow-list).                                    |
| `search_curated_learn`        | Same search, post-filtered to the project's `learn_scopes.yaml`.          |
| `fetch_learn_page`            | Fetch any `learn.microsoft.com` URL as clean markdown (cached 24h).       |
| `search_learn_code_samples`   | Official MS Learn code-snippet search (optional language hint).           |
| `search_github_azure_samples` | Public GitHub repo search across Azure-Samples / Azure / microsoft orgs.  |

## Run it

Stdio (default — what Claude Desktop / Cursor expect):

```pwsh
python -m mcp_servers.learn_browser
```

HTTP (streamable, multi-client):

```pwsh
python -m mcp_servers.learn_browser --http --port 8765
```

## Wire it into an MCP client

### Claude Desktop / Cursor / generic stdio clients

Add to your `claude_desktop_config.json` (or the equivalent for your client):

```jsonc
{
  "mcpServers": {
    "learn-browser": {
      "command": "python",
      "args": ["-m", "mcp_servers.learn_browser"],
      "cwd": "<absolute path to this repo>",
      "env": {
        "LEARN_BROWSER_CACHE_DIR": "<optional override>",
        "GITHUB_TOKEN": "<optional, lifts GitHub rate limit>"
      }
    }
  }
}
```

### VS Code AI Toolkit

The AI Toolkit's MCP picker can attach to either stdio (point it at the same
command above) or HTTP (run with `--http` and point the picker at
`http://127.0.0.1:8765/mcp`).

### The blog-writer workflow

No configuration needed — the workflow imports
`mcp_servers.learn_browser.core` directly, so it gets the same helpers
without paying any IPC overhead.

## Environment

| Var                          | Purpose                                                                |
|------------------------------|------------------------------------------------------------------------|
| `LEARN_BROWSER_CACHE_DIR`    | Override the on-disk cache root (default: `~/.cache/blog-writer/...`). |
| `GITHUB_TOKEN`               | Lifts the GitHub search rate limit from 10 to 30 req/min.              |

## Caching

* `fetch_learn_page` results are cached for **24h**.
* Search results (`search_all_learn`, `search_github_azure_samples`) are
  cached for **6h**.
* Cache is a simple sharded JSON file per query, safe to delete at any time.
