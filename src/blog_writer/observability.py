"""Observability — OpenTelemetry tracing for the blog-writer pipeline.

The Microsoft Agent Framework emits traces, metrics, and logs out of the box
under the ``agent_framework`` and ``gen_ai`` namespaces. We just need to wire
up the exporters once at startup.

Backends supported:

1. **Console** (default for local dev). Enable with
   ``BLOG_WRITER_TRACING_CONSOLE=true`` or by passing ``console=True``.

2. **OTLP** (any OpenTelemetry-compatible collector — Jaeger, Tempo, the
   Azure AI Foundry / AI Toolkit VS Code extension, etc.). Standard
   ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var is honoured by the underlying
   call.

3. **Azure Monitor / Application Insights**. Enable by setting
   ``APPLICATIONINSIGHTS_CONNECTION_STRING``. Requires the optional
   ``azure-monitor-opentelemetry`` package; install with
   ``pip install blog-writer[telemetry]``.

If none of these are configured, telemetry is silently disabled — the
pipeline runs at full speed with zero overhead.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blog_writer.config import AppConfig


logger = logging.getLogger(__name__)


def setup_observability(config: "AppConfig | None" = None) -> bool:
    """Configure OpenTelemetry exporters for the run.

    Returns ``True`` if any exporter was configured, ``False`` if telemetry
    was left off. Safe to call once at process startup; calling it more than
    once is a no-op (the Agent Framework's ``configure_otel_providers`` is
    not idempotent, so we guard with a module-level flag).
    """
    global _configured
    if _configured:
        return True

    enabled_any = False

    # ---- 1. Azure Monitor (App Insights) ----
    appi_conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if appi_conn:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
        except ImportError:
            logger.warning(
                "APPLICATIONINSIGHTS_CONNECTION_STRING is set but "
                "azure-monitor-opentelemetry is not installed. "
                "Run: pip install blog-writer[telemetry]"
            )
        else:
            configure_azure_monitor(connection_string=appi_conn)
            try:
                from agent_framework.observability import enable_sensitive_telemetry

                enable_sensitive_telemetry()
            except ImportError:
                pass
            logger.info("Observability: Azure Monitor configured.")
            enabled_any = True
            _configured = True
            return True

    # ---- 2. OTLP + console via agent-framework's helper ----
    console_env = (os.getenv("BLOG_WRITER_TRACING_CONSOLE") or "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
    )

    if console_env or otlp_endpoint:
        try:
            from agent_framework.observability import configure_otel_providers

            configure_otel_providers(enable_console_exporters=console_env)
            logger.info(
                "Observability: configured (console=%s, otlp_endpoint=%s)",
                console_env,
                otlp_endpoint or "(none)",
            )
            enabled_any = True
        except Exception as exc:  # noqa: BLE001 - never break the pipeline
            logger.warning("Observability setup failed: %s", exc)

    _configured = enabled_any
    if not enabled_any:
        logger.debug(
            "Observability: no exporters configured. "
            "Set BLOG_WRITER_TRACING_CONSOLE=true, OTEL_EXPORTER_OTLP_ENDPOINT, "
            "or APPLICATIONINSIGHTS_CONNECTION_STRING to enable."
        )
    return enabled_any


_configured: bool = False


def reset_for_tests() -> None:
    """Test helper: clear the module-level guard so a fresh setup can run."""
    global _configured
    _configured = False
