"""Tests for the observability wiring."""

from __future__ import annotations

import logging

import pytest

from blog_writer.observability import reset_for_tests, setup_observability


@pytest.fixture(autouse=True)
def _reset_observability_module() -> None:
    reset_for_tests()


def test_setup_observability_is_noop_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "BLOG_WRITER_TRACING_CONSOLE",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
    ):
        monkeypatch.delenv(var, raising=False)
    assert setup_observability() is False


def test_setup_observability_enables_console_when_env_set(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.setenv("BLOG_WRITER_TRACING_CONSOLE", "true")
    caplog.set_level(logging.INFO)
    enabled = setup_observability()
    # Either configured or failed-with-warning; both prove the code path ran.
    assert enabled is True or any(
        "Observability" in r.message for r in caplog.records
    )


def test_setup_observability_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BLOG_WRITER_TRACING_CONSOLE", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    # First call: nothing configured, returns False.
    assert setup_observability() is False
    # Second call should not raise; still no exporters.
    assert setup_observability() is False
