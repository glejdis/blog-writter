"""Tiny eval harness for the blog-writer pipeline.

Loads every ``*.json`` spec in this folder, runs the pipeline once per spec,
and reports a pass/fail table based on simple assertions:

- The angle text contains at least one expected keyword.
- The draft contains every required substring.
- The minimum number of in-scope Learn / external citations is met.
- The final orchestrator verdict is ``approved``.

Run from the repo root::

    python -m evals.runner --stub             # deterministic, no creds
    python -m evals.runner                    # real models (slow, $$)
    python -m evals.runner --only agentic-landing-zones
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from blog_writer.config import load_config
from blog_writer.workflows import BlogState, run_blog_pipeline


EVALS_DIR = Path(__file__).resolve().parent


@dataclass
class EvalSpec:
    id: str
    seed: str
    description: str
    expected_angle_keywords: list[str]
    min_internal_citations: int
    min_external_citations: int
    must_contain_in_draft: list[str]
    stub_compatible: bool

    @classmethod
    def from_json(cls, path: Path) -> "EvalSpec":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            id=str(data["id"]),
            seed=str(data["seed"]),
            description=str(data.get("description", "")),
            expected_angle_keywords=list(data.get("expected_angle_keywords") or []),
            min_internal_citations=int(data.get("min_internal_citations") or 0),
            min_external_citations=int(data.get("min_external_citations") or 0),
            must_contain_in_draft=list(data.get("must_contain_in_draft") or []),
            stub_compatible=bool(data.get("stub_compatible", False)),
        )


@dataclass
class EvalResult:
    spec: EvalSpec
    passed: bool
    reasons: list[str]
    state: BlogState | None


def discover_specs(only: str | None = None) -> list[EvalSpec]:
    specs = [EvalSpec.from_json(p) for p in sorted(EVALS_DIR.glob("*.json"))]
    if only:
        specs = [s for s in specs if s.id == only]
        if not specs:
            sys.exit(f"No eval spec with id '{only}'")
    return specs


def evaluate(spec: EvalSpec, state: BlogState) -> EvalResult:
    reasons: list[str] = []

    angle = (state.angle or "").lower()
    if spec.expected_angle_keywords:
        if not any(kw.lower() in angle for kw in spec.expected_angle_keywords):
            reasons.append(
                f"angle '{state.angle}' missing all keywords {spec.expected_angle_keywords}"
            )

    if len(state.internal_hits) < spec.min_internal_citations:
        reasons.append(
            f"only {len(state.internal_hits)} internal hits "
            f"(<{spec.min_internal_citations})"
        )
    if len(state.external_hits) < spec.min_external_citations:
        reasons.append(
            f"only {len(state.external_hits)} external hits "
            f"(<{spec.min_external_citations})"
        )

    draft = (state.draft or "").lower()
    for needle in spec.must_contain_in_draft:
        if needle.lower() not in draft:
            reasons.append(f"draft missing required substring '{needle}'")

    if state.final_verdict != "approved":
        reasons.append(f"final_verdict={state.final_verdict!r} (expected 'approved')")

    return EvalResult(spec=spec, passed=not reasons, reasons=reasons, state=state)


async def _run_spec(spec: EvalSpec, *, stub: bool) -> EvalResult:
    config = load_config(stub=stub, sandbox="stub" if stub else "local")
    state = await run_blog_pipeline(seed=spec.seed, config=config, autonomous=True)
    return evaluate(spec, state)


async def _run_all(specs: list[EvalSpec], *, stub: bool) -> list[EvalResult]:
    if stub:
        print(
            "\nStub mode: the StubChatClient returns canned responses tuned for "
            "the flagship 'agentic-landing-zones' seed. Seeds without "
            "`stub_compatible: true` will be skipped — run without --stub to "
            "exercise them against real models."
        )

    results: list[EvalResult] = []
    for spec in specs:
        print(f"\n=== {spec.id} ===")
        print(f"seed: {spec.seed}")
        if stub and not spec.stub_compatible:
            print("  -> SKIPPED (not stub_compatible)")
            continue
        try:
            result = await _run_spec(spec, stub=stub)
        except Exception as exc:  # noqa: BLE001
            result = EvalResult(spec=spec, passed=False, reasons=[f"crashed: {exc}"], state=None)
        status = "PASS" if result.passed else "FAIL"
        print(f"  -> {status}")
        for reason in result.reasons:
            print(f"     - {reason}")
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run blog-writer eval seeds.")
    parser.add_argument(
        "--stub",
        action="store_true",
        help="Run all seeds in stub mode (no model calls).",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Run only the spec with this id.",
    )
    args = parser.parse_args()

    specs = discover_specs(only=args.only)
    if not specs:
        sys.exit("No eval specs found in evals/")

    results = asyncio.run(_run_all(specs, stub=args.stub))

    passed = sum(1 for r in results if r.passed)
    print(f"\nSummary: {passed}/{len(results)} eval(s) passed.")
    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
