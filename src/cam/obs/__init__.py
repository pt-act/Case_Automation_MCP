"""Observability bootstrap — structlog + OpenTelemetry + Prometheus + PII scrubber."""

from cam.obs.observability import (
    bind_run_id,
    configure_observability,
    get_meter,
    get_run_id,
    get_tracer,
    pii_scrub,
    sanitize_label,
)

__all__ = [
    "bind_run_id",
    "configure_observability",
    "get_meter",
    "get_run_id",
    "get_tracer",
    "pii_scrub",
    "sanitize_label",
    "span_attributes_for_run",
    "start_workflow_span",
    "trace_connector_call",
]
