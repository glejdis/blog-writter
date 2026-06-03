"""Tests for the PoC builder retry prompt and result wiring."""

from __future__ import annotations

from blog_writer.tools.code_sandbox import SandboxResult
from blog_writer.workflows.blog_pipeline import _poc_prompt
from blog_writer.workflows.state import PoCSpec

SPEC = PoCSpec(
    id="agent-demo",
    section="Building the agent",
    description="A minimal agent that says hello.",
    language="python",
)


def test_first_attempt_prompt_omits_failure_context() -> None:
    prompt = _poc_prompt(SPEC, attempt=1, previous=None, previous_code="ignored")
    assert "Previous attempt" not in prompt
    assert "stderr" not in prompt
    assert "A minimal agent" in prompt


def test_retry_prompt_includes_failure_context() -> None:
    previous = SandboxResult(
        exit_code=1,
        stdout="partial",
        stderr="Traceback: NameError: name 'foo' is not defined",
        duration_seconds=0.1,
    )
    prompt = _poc_prompt(
        SPEC,
        attempt=2,
        previous=previous,
        previous_code="print(foo)\n",
    )
    assert "Previous attempt 1 failed" in prompt
    assert "exit code 1" in prompt
    assert "NameError" in prompt
    assert "print(foo)" in prompt
    assert "Fix the bug" in prompt


def test_retry_prompt_truncates_long_stderr() -> None:
    huge_stderr = "X" * 10_000
    previous = SandboxResult(
        exit_code=1, stdout="", stderr=huge_stderr, duration_seconds=0.1
    )
    prompt = _poc_prompt(SPEC, attempt=3, previous=previous, previous_code="bad\n")
    # Hard cap at 2000 chars in the prompt; should NOT include the full stderr.
    assert huge_stderr not in prompt
    assert "XXXX" in prompt  # but a chunk is included
