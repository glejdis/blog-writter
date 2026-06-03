# blog-writer eval seeds

These are concrete starting points you can run to see what the system produces.
Each `*.json` file in this directory is a self-contained eval spec.

## Spec format

```json
{
  "id": "short-kebab-id",
  "seed": "Free-text seed topic the editorial team gets.",
  "description": "Why we care about this scenario.",
  "expected_angle_keywords": ["landing zone", "agent"],
  "min_internal_citations": 1,
  "min_external_citations": 0,
  "must_contain_in_draft": ["Cloud Adoption Framework"],
  "stub_compatible": false
}
```

`stub_compatible: true` lets the spec run under `--stub`. The stub chat client
returns canned responses (tuned for the "landing zones for agentic workloads"
scenario), so only seeds whose assertions match those canned responses will
pass in stub mode. Real-mode runs ignore this flag and execute every spec.

## Run one

```powershell
# Real-mode run (needs creds — produces real content):
blog-writer new --seed "Landing zones for AI agent workloads on Azure" --autonomous

# Stub run (deterministic, no creds needed — for sanity checks):
blog-writer new --seed "Landing zones for AI agent workloads on Azure" --stub --autonomous
```

## Run the eval harness

```powershell
# Stub mode (always works):
python -m evals.runner --stub

# Real mode (uses your configured provider; slower, costs API tokens):
python -m evals.runner
```

The harness loads every `*.json` in this folder, runs the pipeline once per
seed, and prints a pass/fail table based on the assertions in the spec.
