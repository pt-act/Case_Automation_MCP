"""Prometheus metrics for the workflow orchestration engine — spec G11."""

from __future__ import annotations

from typing import Any

_runs_total: Any = None
_step_retries_total: Any = None
_gate_dwell_seconds: Any = None
_parked_backlog: Any = None
_approval_decisions_total: Any = None
_idem_short_circuits_total: Any = None
_resume_recoveries_total: Any = None
_token_verify_failures_total: Any = None


def _init() -> None:
    global _runs_total, _step_retries_total, _gate_dwell_seconds, _parked_backlog
    global _approval_decisions_total, _idem_short_circuits_total
    global _resume_recoveries_total, _token_verify_failures_total
    try:
        from prometheus_client import Counter, Gauge, Histogram

        if _runs_total is None:
            _runs_total = Counter(
                "cam_workflow_runs_total",
                "Total workflow runs by status.",
                ["workflow", "status"],
            )
            _step_retries_total = Counter(
                "cam_workflow_step_retries_total",
                "Total step retry attempts.",
                ["workflow", "step"],
            )
            _gate_dwell_seconds = Histogram(
                "cam_workflow_gate_dwell_seconds",
                "Time a run spent awaiting gate approval.",
                ["workflow"],
                buckets=[60, 300, 900, 3600, 14400, 86400],
            )
            _parked_backlog = Gauge(
                "cam_workflow_parked_backlog",
                "Number of runs currently in parked state.",
            )
            _approval_decisions_total = Counter(
                "cam_workflow_approval_decisions_total",
                "Gate approval/rejection decisions by channel.",
                ["channel", "decision"],
            )
            _idem_short_circuits_total = Counter(
                "cam_workflow_idempotency_short_circuits_total",
                "Steps skipped due to idempotency (resume path).",
            )
            _resume_recoveries_total = Counter(
                "cam_workflow_resume_recoveries_total",
                "Runs re-enqueued during crash-recovery sweep.",
            )
            _token_verify_failures_total = Counter(
                "cam_workflow_token_verify_failures_total",
                "Gate token verification failures (tamper/expired/reused).",
                ["reason"],
            )
    except Exception as _exc:
        import structlog as _sl
        _sl.get_logger(__name__).debug("metric_init_failed", error=str(_exc))


def record_run_status(workflow: str, status: str) -> None:
    _init()
    try:
        _runs_total.labels(workflow=workflow, status=status).inc()
        if status == "parked":
            _parked_backlog.inc()
        elif status == "succeeded":
            _parked_backlog.dec()
    except Exception as _exc:
        import structlog as _sl
        _sl.get_logger(__name__).debug("metric_init_failed", error=str(_exc))


def record_step_retry(workflow: str, step: str) -> None:
    _init()
    try:
        _step_retries_total.labels(workflow=workflow, step=step).inc()
    except Exception as _exc:
        import structlog as _sl
        _sl.get_logger(__name__).debug("metric_init_failed", error=str(_exc))


def record_gate_dwell(workflow: str, dwell_seconds: float) -> None:
    _init()
    try:
        _gate_dwell_seconds.labels(workflow=workflow).observe(dwell_seconds)
    except Exception as _exc:
        import structlog as _sl
        _sl.get_logger(__name__).debug("metric_init_failed", error=str(_exc))


def record_approval_decision(channel: str, decision: str) -> None:
    _init()
    try:
        _approval_decisions_total.labels(channel=channel, decision=decision).inc()
    except Exception as _exc:
        import structlog as _sl
        _sl.get_logger(__name__).debug("metric_init_failed", error=str(_exc))


def record_token_verify_failure(reason: str) -> None:
    _init()
    try:
        _token_verify_failures_total.labels(reason=reason).inc()
    except Exception as _exc:
        import structlog as _sl
        _sl.get_logger(__name__).debug("metric_init_failed", error=str(_exc))


def record_idem_short_circuit() -> None:
    _init()
    try:
        _idem_short_circuits_total.inc()
    except Exception as _exc:
        import structlog as _sl
        _sl.get_logger(__name__).debug("metric_init_failed", error=str(_exc))


def record_resume_recovery() -> None:
    _init()
    try:
        _resume_recoveries_total.inc()
    except Exception as _exc:
        import structlog as _sl
        _sl.get_logger(__name__).debug("metric_init_failed", error=str(_exc))
