# blog-writer

A multi-agent system that researches, plans, and writes technical blog posts on
Azure infrastructure and agentic AI topics. Built on the **Microsoft Agent
Framework** (Python) and grounded in **Microsoft Learn** (Cloud Adoption
Framework, Well-Architected, Architecture Center, AI Foundry docs) as the
"internal best practices" source.

> **New here?** Go to **[Prerequisites](#prerequisites)** → **[Quickstart](#quickstart)**
> to get a run going in a couple of minutes (no cloud credentials needed), then
> **[Configure](#configure)** when you want real output.

## Contents

- [What it does](#what-it-does)
- [Agents](#agents) · [Pipeline](#pipeline)
- [Prerequisites](#prerequisites)
- [Quickstart](#quickstart)
- [Configure](#configure)
- [Generate a post](#run)
- [Improve an existing draft](#improve-an-existing-draft)
- [Chat UI](#chat-ui)
- [Test](#test) · [Eval seeds](#eval-seeds) · [Observability](#observability)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)

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
| Research | External research — defaults to the Foundry `o3-deep-research` model (agentic, Bing-grounded), falling back to a broad MS Learn + Azure-Samples search via the custom Learn Browser MCP | `o3-deep-research` → `gpt-5.4` fallback |
| Planner | Outline + PoC requirements | reasoning model (`o4-mini`) |
| PoC Builder | Generate code + run in sandbox + capture output | `gpt-5.3-codex` |
| Diagrammer | Generate an Excalidraw + Mermaid architecture diagram per post | `gpt-5.4` |
| Stylist | Learn a house writing style from example posts (`knowledge_base/style_corpus/`) → Style Card the Writer follows | `gpt-5-mini` |
| Writer | Long-form prose, Learn-first citations | `claude-opus-4-7` or `gpt-5.5` |
| Fact-Checker | Verify each claim against a source | `gpt-5-mini` |
| Critic | Score draft vs. rubric, request revisions | `gpt-5.4` |

All model assignments are swappable in
[`src/blog_writer/models/config.py`](src/blog_writer/models/config.py) or via
the `BLOG_WRITER_MODEL_*` environment variables.

> **Deep research is on by default.** The external-research stage uses the
> Foundry `o3-deep-research` model (agentic, Bing-grounded) whenever the
> `AZURE_AI_DEEP_RESEARCH_ENDPOINT`, `AZURE_AI_DEEP_RESEARCH_MODEL`, and
> `AZURE_AI_BING_CONNECTION_ID` env vars are set. When they're absent it
> automatically falls back to the lightweight Learn Browser MCP search, so
> nothing breaks if you haven't provisioned an `o3-deep-research` deployment.
> Opt out entirely with `BLOG_WRITER_DEEP_RESEARCH=false` (or
> `--no-deep-research` on `blog-writer improve`).

## Pipeline

```
Seed
 → Ideation
 → [HUMAN: pick angle]                (skip with --autonomous)
 → Internal Knowledge ‖ Research      (in parallel)
 → Planner (outline + PoC specs)
 → [HUMAN: approve plan]              (skip with --autonomous)
 → PoC Builder (generate + execute samples)
 → Stylist (learn house style from knowledge_base/style_corpus/)
 → Writer (draft)
 → Fact-Checker
 → Critic   ──┐  (loop until threshold or max revisions)
 ←───────────┘
 → Orchestrator (final review)
 → drafts/<slug>.md + samples/<slug>/ + sources.json
```

## Prerequisites

| Tool | Version | Check |
|---|---|---|
| **Python** | 3.10+ (3.13 recommended) | `python --version` |
| **[uv](https://docs.astral.sh/uv/)** | latest | `uv --version` |
| **git** | any | `git --version` |

Install **uv** (the fast Python package manager used below) if you don't have it:

```pwsh
winget install --id=astral-sh.uv -e                  # Windows
# brew install uv                                    # macOS
# curl -LsSf https://astral.sh/uv/install.sh | sh    # Linux
```

You do **not** need any cloud credentials to try the project — it ships a
**stub mode** that runs the whole pipeline with fake agents and no network
calls. Real output needs an Azure AI Foundry project *or* an OpenAI / Azure
OpenAI key (see [Configure](#configure)).

> **ARM64 Windows:** the repo ships a `constraints.txt` pinning
> `cryptography==46.0.3` because newer releases lack ARM64 wheels. The commands
> below already apply it — keep the `--constraint` / `-c` flag.

## Quickstart

```pwsh
# 1. Clone
git clone https://github.com/glejdis/blog-writter.git
cd blog-writter

# 2. Create + activate a virtual environment
uv venv .venv --python 3.13
.\.venv\Scripts\Activate.ps1          # Windows PowerShell
# source .venv/bin/activate           # macOS / Linux

# 3. Install the project (editable) with dev + UI extras
uv pip install --prerelease=allow --constraint constraints.txt -e ".[dev,ui]"

# 4. Smoke-test with NO credentials and NO network (stub mode)
blog-writer new --seed "agentic workloads on Azure landing zones" --stub
```

That last command writes a stub draft to `drafts/` and sample PoCs to
`samples/` — proof the wiring works end-to-end. When you're ready for real
output, head to [Configure](#configure).

<details>
<summary>Prefer plain <code>pip</code> instead of <code>uv</code>?</summary>

```pwsh
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install --pre -c constraints.txt -e ".[dev,ui]"
```
</details>

## Configure

For real (non-stub) runs, copy the example env file and fill in **one** model
backend:

```pwsh
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux
```

The minimum is **one** of:

- **Azure AI Foundry** (preferred — hosts GPT-5.x, Claude, and reasoning
  models): set `BLOG_WRITER_PROVIDER=foundry` and
  `AZURE_AI_PROJECT_ENDPOINT=https://<project>.services.ai.azure.com/api/projects/<project>`.
  Foundry uses your `az login` credentials — no API key in the file.
- **OpenAI** (quickest for local dev): set `BLOG_WRITER_PROVIDER=openai` and
  `OPENAI_API_KEY=sk-...`.
- **Azure OpenAI**: set `BLOG_WRITER_PROVIDER=azure_openai` plus
  `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY`.

Per-agent model deployment names and every workflow knob (revision cap, critic
threshold, citation limits, …) are documented inline in
[`.env.example`](.env.example) and overridable with `BLOG_WRITER_*` env vars.
Leave the file empty to stay in stub mode.

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

### Writing style (Stylist agent)

The **Stylist agent** teaches the Writer your publication's voice. Drop example
posts (e.g. *The Cloud Wire*) as `.md` files into
[`knowledge_base/style_corpus/`](knowledge_base/style_corpus/); before drafting,
the Stylist reads them, distills a **Style Card** (voice, structure, sentence
rules, banned clichés), and the Writer follows it — for *new* posts and
`improve` runs alike. The card shapes *how* the post reads; it never overrides
the Learn-first citation or fact-checking rules.

The folder ships with a structural-patterns reference and one sample post, so the
stage works out of the box. Add your own examples to steer the voice, or turn the
stage off with `BLOG_WRITER_STYLE=false`.

## Run

```pwsh
# Interactive run with two human checkpoints
blog-writer new --seed "agentic workloads on Azure landing zones"

# Fully autonomous (no human checkpoints)
blog-writer new --seed "AI gateway patterns on Azure" --autonomous

# Stub mode — no model calls, no network. Useful for verifying the wiring.
blog-writer new --seed "anything" --stub
```

## Improve an existing draft

Already have a draft? Point `blog-writer improve` at it. Instead of writing a new
post from a seed, it reads the file, finds sources keyed off the draft's **own
title and section headings** (curated MS Learn first, then external/deep
research), fact-checks and critiques it, and rewrites it to weave in Learn-first
inline citations (`[n](url)`) plus a numbered `## Sources` list — preserving your
structure and voice.

```pwsh
# Find sources, recommend improvements, and rewrite with citations.
blog-writer improve drafts/landing-zone-to-secure-ai-part-1.md

# Deep research (Foundry o3-deep-research, Bing-grounded) is ON by default.
# Opt out and use the lightweight Learn/GitHub search instead:
blog-writer improve drafts/my-post.md --no-deep-research

# Just the recommendations + sources — don't touch the prose.
blog-writer improve drafts/my-post.md --recommend-only

# Verify the wiring without model calls.
blog-writer improve drafts/my-post.md --stub
```

Outputs land next to the input draft:

| File | Contents |
|---|---|
| `<draft>.improved.md` | The rewritten draft with citations (skipped with `--recommend-only`) |
| `<draft>.review.md` | Critic score, recommended improvements, fact-check findings, ranked sources |
| `<draft>.sources.json` | Citations + recommendations + fact-check findings (machine-readable) |

Use `--output/-o` to choose where the improved draft is written, and `--topic`
to override the search subject (default: derived from the draft).

## Chat UI

For an interactive experience — fill in a brief, watch each agent's progress
live, answer human checkpoints in the browser, and revise the draft until
you're happy — launch the built-in web UI:

```pwsh
# The UI extras (FastAPI + websockets) are already installed if you ran the
# Quickstart with ".[dev,ui]". Otherwise add them:
uv pip install --constraint constraints.txt -e ".[ui]"

# Start the server on http://127.0.0.1:8000
blog-writer ui

# Or pick a different host/port
blog-writer ui --host 0.0.0.0 --port 8080

# Developing the pipeline? Auto-restart on Python file changes:
blog-writer ui --reload
```

> **Heads-up for contributors:** without `--reload`, uvicorn loads the Python
> modules once at startup, so edits to pipeline/agent code won't take effect
> until you stop and restart the server. Static assets (HTML/JS/CSS) *do*
> refresh on a browser reload.

The UI is a single-page chat app built on FastAPI + WebSockets and vanilla
JS. The left pane has a **mode toggle**: *New post* (the brief form — topic,
optional TOC, extra instructions, autonomous/stub toggles) or *Improve a
draft* (paste Markdown or point at a server file path, with deep-research /
recommend-only / stub options). A live stage indicator sits below. The right
pane is the chat transcript plus the rendered draft. After a run finishes,
type free-form revision instructions into the bar at the bottom of the chat to
iterate; each turn re-runs the Writer (and optionally the Fact-Checker +
Critic) and ships an updated draft back into the UI. New posts persist to
`drafts/<slug>.md`; improve runs persist `drafts/<slug>.improved.md`,
`.review.md`, and `.sources.json` — the chat shows the saved paths each time.

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

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cryptography` fails to build / no wheel on **ARM64 Windows** | Make sure you passed `--constraint constraints.txt` (uv) or `-c constraints.txt` (pip) — it pins `cryptography==46.0.3`, the last version with ARM64 wheels. |
| **UI doesn't reflect code changes** | uvicorn loads Python modules once at startup. Stop the server (Ctrl+C) and restart, or run `blog-writer ui --reload`. Static files (HTML/JS/CSS) refresh on a browser reload. |
| `408 Timeout` / `429` / `5xx` from the model mid-run | These transient errors are retried automatically with exponential backoff. A persistent failure usually means the Foundry / OpenAI endpoint is throttling or out of capacity — check your quota. |
| `blog-writer: command not found` | Activate the virtual environment first (`.\.venv\Scripts\Activate.ps1`), or run via `python -m blog_writer.cli`. |
| `UI dependencies are missing` | Install the UI extras: `uv pip install -e ".[ui]"`. |
| Garbled symbols in the console on **legacy Windows** | The CLI forces UTF-8 output; if your terminal still mangles glyphs, use Windows Terminal or run `chcp 65001`. |
| Want to run with no cloud/API setup | Add `--stub` to any command, or set `BLOG_WRITER_STUB=true`. |

## Project layout

```
src/blog_writer/
  agents/         # one file per agent role
  tools/          # learn_mcp, bing_search, code_sandbox, fs
  workflows/      # the orchestration graph + shared state type
  models/         # per-agent model assignments + provider factories
  prompts/        # versioned system prompts (one .md per agent)
  observability.py
  config.py
  cli.py          # `blog-writer new` and `blog-writer ui`
mcp_servers/
  learn_browser/  # custom MCP server wrapping the official MS Learn MCP
ui/
  server.py       # FastAPI app + /ws WebSocket protocol
  static/         # vanilla-JS single-page chat UI
knowledge_base/
  learn_scopes.yaml   # allow-list of MS Learn root paths
  style_corpus/       # example posts the Stylist learns the house style from
evals/            # sample seed topics + tiny eval harness
samples/          # generated PoCs
drafts/           # generated drafts
tests/
```
