"""Observability bootstrap — structlog, OpenTelemetry, Prometheus, PII scrubber.

One call to configure_observability() wires all three signals with a shared
PII redaction ruleset.  PII redacted in logs is redacted in traces and metric
labels (spec §4.5, §4.6).
"""

from __future__ import annotations

import re
import threading
from contextvars import ContextVar
from typing import Any

import structlog
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import Counter

from cam.packs.base import ENGINE_PII_BASELINE, PackSelectionError, active_pii_patterns

# ---------------------------------------------------------------------------
# PII patterns — composed as engine_baseline ∪ active-pack additions.
#
# The engine baseline floor (email/phone/SSN/DOB) is non-removable and lives in
# `cam.packs.base.ENGINE_PII_BASELINE`. Domain-specific patterns (e.g. the
# immigration pack's A-number + passport) are supplied by the active pack and
# composed in by `configure_pii_patterns()` at startup (G6). Until a pack is
# selected this falls back to the baseline floor — never less.
# ---------------------------------------------------------------------------

_DEFAULT_PII_PATTERNS: list[re.Pattern[str]] = list(ENGINE_PII_BASELINE)

_REDACTED = "[REDACTED]"
_pii_patterns: list[re.Pattern[str]] = list(_DEFAULT_PII_PATTERNS)


def configure_pii_patterns(pack: Any = None) -> None:
    """Set the active redaction set to `engine baseline ∪ active-pack additions`.

    Call at startup after the domain pack is selected. With no pack active, falls
    back to the engine baseline floor (the floor is never removed)."""
    global _pii_patterns
    try:
        _pii_patterns = active_pii_patterns(pack)
    except PackSelectionError:
        _pii_patterns = list(ENGINE_PII_BASELINE)

# Prometheus counter for PII redactions
_pii_redactions_total: Counter | None = None

# ---------------------------------------------------------------------------
# run_id context variable
# ---------------------------------------------------------------------------

_run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)


def bind_run_id(run_id: str) -> None:
    """Bind a run_id to the current async context."""
    _run_id_var.set(run_id)


def get_run_id() -> str | None:
    return _run_id_var.get()


# ---------------------------------------------------------------------------
# PII scrubbing utilities
# ---------------------------------------------------------------------------


def pii_scrub(value: str) -> str:
    """Redact PII patterns from a string value."""
    for pattern in _pii_patterns:
        value = pattern.sub(_REDACTED, value)
    return value


def _scrub_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact PII from a dict (used for span attributes)."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = pii_scrub(v)
        elif isinstance(v, dict):
            out[k] = _scrub_dict(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Structlog PII processor
# ---------------------------------------------------------------------------


def _pii_structlog_processor(
    logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    global _pii_redactions_total
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            scrubbed = pii_scrub(value)
            if scrubbed != value:
                event_dict[key] = scrubbed
                if _pii_redactions_total is not None:
                    _pii_redactions_total.inc()
    return event_dict


def _run_id_processor(
    logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    run_id = get_run_id()
    if run_id is not None:
        event_dict["run_id"] = run_id
    return event_dict


# ---------------------------------------------------------------------------
# OpenTelemetry PII span processor
# ---------------------------------------------------------------------------


class PIISpanProcessor(SpanProcessor):
    """Redacts PII from span attributes before they are exported."""

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        pass

    def on_end(self, span: Any) -> None:
        if not span.attributes:
            return
        clean = _scrub_dict(dict(span.attributes))
        span._attributes = clean

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


# ---------------------------------------------------------------------------
# Metric label sanitiser
# ---------------------------------------------------------------------------


def sanitize_label(value: str) -> str:
    """Drop high-cardinality PII from a Prometheus label value."""
    scrubbed = pii_scrub(value)
    if len(scrubbed) > 64:
        scrubbed = scrubbed[:61] + "..."
    return scrubbed


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

_configured = threading.Lock()
_is_configured = False


def configure_observability(
    service_name: str = "case-automation-mcp",
    log_level: str = "INFO",
    otel_endpoint: str | None = None,
    extra_pii_patterns: list[str] | None = None,
) -> None:
    """Wire structlog, OpenTelemetry, and Prometheus in one call.
    Idempotent — safe to call multiple times (subsequent calls are no-ops).
    """
    global _is_configured, _pii_redactions_total

    with _configured:
        if _is_configured:
            return

        # Extra PII patterns from config
        if extra_pii_patterns:
            for pat in extra_pii_patterns:
                _pii_patterns.append(re.compile(pat))

        # --- structlog ---
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                _run_id_processor,
                _pii_structlog_processor,
                structlog.stdlib.add_log_level,
                # NOTE: add_logger_name removed — requires stdlib Logger; we use PrintLoggerFactory
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(__import__("logging"), log_level.upper(), 20)
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )

        # --- OpenTelemetry traces ---
        resource = Resource.create({SERVICE_NAME: service_name})
        tracer_provider = TracerProvider(resource=resource)

        tracer_provider.add_span_processor(PIISpanProcessor())

        if otel_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

                exporter = OTLPSpanExporter(endpoint=otel_endpoint)
                tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
            except ImportError:
                # No OTLP exporter — use in-memory sink (avoids console I/O errors on shutdown)
                tracer_provider.add_span_processor(
                    SimpleSpanProcessor(InMemorySpanExporter())
                )
        else:
            # No endpoint configured — use in-memory sink
            tracer_provider.add_span_processor(
                SimpleSpanProcessor(InMemorySpanExporter())
            )

        trace.set_tracer_provider(tracer_provider)

        # --- OpenTelemetry metrics ---
        reader = PeriodicExportingMetricReader(ConsoleMetricExporter(),
            export_interval_millis=60_000)
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)

        # --- Prometheus ---
        from prometheus_client import Counter as PCounter

        _pii_redactions_total = PCounter(
            "cam_pii_redactions_total",
            "Total number of PII redactions applied across all signals.",
        )

        _is_configured = True


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


def get_meter(name: str) -> metrics.Meter:
    return metrics.get_meter(name)


# ---------------------------------------------------------------------------
# Context propagation helpers
# ---------------------------------------------------------------------------


def span_attributes_for_run(run_id: str | None, workflow: str | None = None) -> dict[str, str]:
    """Return standard span attributes for a workflow run — PII-free."""
    attrs: dict[str, str] = {}
    if run_id:
        attrs["cam.run_id"] = run_id
    if workflow:
        attrs["cam.workflow"] = workflow
    return attrs


def start_workflow_span(
    workflow_name: str,
    run_id: str | None = None,
    step: str | None = None,
) -> trace.Span:
    """Start a span for a workflow step, correlated by run_id.

    Usage::

        with start_workflow_span("intake", run_id=ctx.run_id, step="parse_lead") as span:
            # span is active; child spans from connectors are automatically parented
            result = await connector.call()
    """
    tracer = get_tracer(f"cam.workflow.{workflow_name}")
    span_name = f"{workflow_name}.{step}" if step else workflow_name
    span = tracer.start_span(
        span_name,
        attributes=span_attributes_for_run(run_id, workflow_name),
    )
    return span


def trace_connector_call(connector: str, operation: str) -> trace.Span:
    """Start a span for a connector call — child of the active workflow span."""
    tracer = get_tracer(f"cam.connector.{connector}")
    return tracer.start_span(
        f"{connector}.{operation}",
        attributes={"cam.connector": connector, "cam.operation": operation},
    )
