"""Sandbox execution for PoC code.

Three modes (selected by `AppConfig.sandbox`):

- `local`  — run the generated code in a fresh subprocess using the host's
  Python interpreter, in a scratch directory, with a wall-clock timeout.
  Good for dev. Not isolated — don't use with untrusted code.
- `aca`    — Azure Container Apps dynamic sessions. **Stubbed in this phase**;
  wired up in a later phase.
- `stub`   — no execution; returns a canned successful result. Used by the
  smoke test.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from blog_writer.config import AppConfig


@dataclass(frozen=True)
class SandboxResult:
    """Captured output of a single sandboxed execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    artifacts: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


async def run_in_sandbox(
    code: str,
    *,
    language: str,
    config: AppConfig,
    timeout_seconds: int = 30,
    extra_files: dict[str, str] | None = None,
) -> SandboxResult:
    """Execute `code` in the configured sandbox and return the captured result."""
    if config.sandbox == "stub":
        return _stub_result()
    if config.sandbox == "aca":
        # Wired up in a later phase. For now, behave like stub but mark it
        # clearly so a real run notices.
        return _stub_result(stdout="[aca sandbox not yet implemented — stub result]")
    if config.sandbox == "local":
        return await _run_local(
            code=code,
            language=language,
            timeout_seconds=timeout_seconds,
            extra_files=extra_files or {},
        )
    raise ValueError(f"Unknown sandbox mode: {config.sandbox}")


def _stub_result(stdout: str = "stub sandbox: ok") -> SandboxResult:
    return SandboxResult(
        exit_code=0,
        stdout=stdout,
        stderr="",
        duration_seconds=0.0,
        artifacts=[],
    )


async def _run_local(
    *,
    code: str,
    language: str,
    timeout_seconds: int,
    extra_files: dict[str, str],
) -> SandboxResult:
    if language not in {"python", "py"}:
        return SandboxResult(
            exit_code=2,
            stdout="",
            stderr=f"Local sandbox only supports Python (got: {language})",
            duration_seconds=0.0,
        )

    with tempfile.TemporaryDirectory(prefix="blog-writer-sbx-") as tmp:
        tmp_dir = Path(tmp)
        for rel, content in extra_files.items():
            (tmp_dir / rel).write_text(content, encoding="utf-8")
        script = tmp_dir / "poc.py"
        script.write_text(code, encoding="utf-8")
        loop = asyncio.get_event_loop()
        start = loop.time()
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=tmp_dir,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                exit_code=124,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + f"\n[timed out after {timeout_seconds}s]",
                duration_seconds=timeout_seconds,
            )
        duration = loop.time() - start
        return SandboxResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_seconds=duration,
        )
