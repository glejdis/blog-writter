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
4. Fills external gaps via Bing grounding (recent blog posts, GitHub samples).
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
| Research | Bing-grounded external search, fills gaps | `gpt-5.4` |
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
pytest
```

## Layout

```
src/blog_writer/
  agents/         # one file per agent role
  tools/          # learn_mcp, bing_search, code_sandbox, fs
  workflows/      # the orchestration graph + shared state type
  models/         # per-agent model assignments + provider factories
  prompts/        # versioned system prompts (one .md per agent)
  config.py
  cli.py
knowledge_base/
  learn_scopes.yaml   # allow-list of MS Learn root paths
samples/          # generated PoCs
drafts/           # generated drafts
tests/
```
