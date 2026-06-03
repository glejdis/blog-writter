# blog-writer

A multi-agent system that researches, plans, and writes technical blog posts on
Azure infrastructure and agentic AI topics. Built on the **Microsoft Agent
Framework** (Python) and grounded in **Microsoft Learn** (Cloud Adoption
Framework, Well-Architected, Architecture Center, AI Foundry docs) as the
"internal best practices" source.

## What it does

1. Takes a seed topic (e.g. *"agentic workloads on Azure landing zones"*).
2. Generates 3–5 concrete angles. A human picks one.
3. Pulls authoritative best-practice content from Microsoft Learn via the
   official **MS Learn Docs MCP server**, scoped to a curated allow-list of
   high-signal Learn roots (CAF / WAF / Architecture Center / AI Foundry).
4. Fills external gaps via the **custom Learn Browser MCP server** (broad
   MS Learn search + optional Azure-Samples GitHub repos).
5. Drafts an outline and a list of PoCs the blog will need. A human approves.
6. Generates the PoCs as small runnable code, executes them in a sandbox,
   and captures the verified output.
7. Writes the draft, citing **Learn first** and weaving in the verified PoC
   output.
8. Fact-checks every concrete claim against its source.
9. The Critic scores the draft against a rubric and loops back to the Writer
   until the threshold is met or the revision cap is hit.
10. Final deliverable: `drafts/<slug>.md`, `samples/<slug>/`, `sources.json`.

## Agents

| Agent | Role | Default model |
|---|---|---|
| Orchestrator (Editor-in-Chief) | Final review of assembled draft + samples | `gpt-5.4` |
| Ideation | Seed → 3–5 angles | `gpt-5-mini` |
| Internal Knowledge | MS Learn MCP search + scope filtering + summarize | `gpt-5-mini` |
| Research | Broad MS Learn + Azure-Samples search via the custom Learn Browser MCP | `gpt-5.4` |
| Planner | Outline + PoC requirements | reasoning model (`o4-mini`) |
| PoC Builder | Generate code + run in sandbox + capture output | `gpt-5.3-codex` |
| Writer | Long-form prose, Learn-first citations | `claude-opus-4-7` or `gpt-5.5` |
| Fact-Checker | Verify each claim against a source | `gpt-5-mini` |
| Critic | Score draft vs. rubric, request revisions | `gpt-5.4` |

All model assignments are swappable in
[`src/blog_writer/models/config.py`](src/blog_writer/models/config.py) or via
the `BLOG_WRITER_MODEL_*` environment variables.

## Pipeline

```
Seed
 → Ideation
 → [HUMAN: pick angle]                (skip with --autonomous)
 → Internal Knowledge ‖ Research      (in parallel)
 → Planner (outline + PoC specs)
 → [HUMAN: approve plan]              (skip with --autonomous)
 → PoC Builder (generate + execute samples)
 → Writer (draft)
 → Fact-Checker
 → Critic   ──┐  (loop until threshold or max revisions)
 ←───────────┘
 → Orchestrator (final review)
 → drafts/<slug>.md + samples/<slug>/ + sources.json
```

## Install

ARM64 Windows users: this repo ships a `constraints.txt` pinning
`cryptography==46.0.3` because newer versions don't have ARM64 wheels.

```pwsh
uv venv .venv --python 3.13
.\.venv\Scripts\Activate.ps1
uv pip install --prerelease=allow --constraint constraints.txt -e ".[dev]"
```

## Configure

Copy `.env.example` to `.env` and fill in (at minimum) your Azure AI Foundry
project endpoint or an OpenAI API key. Leave everything empty to run in stub
mode.

### External search (Research agent)

The Research agent is backed by the project's **custom Learn Browser MCP
server** (see [`mcp_servers/learn_browser/`](mcp_servers/learn_browser/)),
which wraps the official Microsoft Learn Docs MCP and adds project-specific
scoping, caching, and an optional Azure-Samples GitHub search.

**No API keys are required.** Optional knobs:

| Env var                   | Purpose                                                                  |
|---------------------------|--------------------------------------------------------------------------|
| `GITHUB_TOKEN`            | Lifts the public GitHub search rate limit (10 → 30 req/min).             |
| `LEARN_BROWSER_CACHE_DIR` | Override the on-disk cache root (default: `~/.cache/blog-writer/...`).   |

The same server can be launched standalone and attached to Claude Desktop,
VS Code AI Toolkit, Cursor, or any other MCP client — see the [server
README](mcp_servers/learn_browser/README.md) for instructions.

## Run

```pwsh
# Interactive run with two human checkpoints
blog-writer new --seed "agentic workloads on Azure landing zones"

# Fully autonomous (no human checkpoints)
blog-writer new --seed "AI gateway patterns on Azure" --autonomous

# Stub mode — no model calls, no network. Useful for verifying the wiring.
blog-writer new --seed "anything" --stub
```

## Test

```pwsh
# Unit tests (fast, no network).
pytest

# Live integration test that hits the real MS Learn MCP server.
$env:RUN_INTEGRATION_TESTS = "1"; pytest tests/test_learn_mcp_integration.py -v
```

## Eval seeds

The [`evals/`](evals/) folder ships sample seed topics with assertions
about the produced draft (expected angle keywords, minimum citation counts,
required substrings). Run them all with:

```pwsh
python -m evals.runner --stub        # quick sanity check
python -m evals.runner               # real models — slower, costs tokens
python -m evals.runner --only agentic-landing-zones
```

## Observability

The Microsoft Agent Framework emits OpenTelemetry traces, metrics, and logs
out of the box. The CLI calls `setup_observability()` at startup and wires
exporters based on environment variables:

| Env var | Effect |
|---|---|
| `BLOG_WRITER_TRACING_CONSOLE=true` | Print spans / metrics to the console |
| `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317` | Ship to any OTLP collector (Jaeger / Tempo / AI Toolkit / OTel Collector) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING=...` | Ship to Azure Monitor — install with `pip install -e ".[telemetry]"` |

If none are set, telemetry is silently disabled.

## Layout

```
src/blog_writer/
  agents/         # one file per agent role
  tools/          # learn_mcp, bing_search, code_sandbox, fs
  workflows/      # the orchestration graph + shared state type
  models/         # per-agent model assignments + provider factories
  prompts/        # versioned system prompts (one .md per agent)
  observability.py
  config.py
  cli.py
knowledge_base/
  learn_scopes.yaml   # allow-list of MS Learn root paths
evals/            # sample seed topics + tiny eval harness
samples/          # generated PoCs
drafts/           # generated drafts
tests/
```
