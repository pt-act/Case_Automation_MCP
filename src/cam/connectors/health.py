"""ConnectorHealth circuit-breaker state machine — spec §5.4.

States:  healthy → degraded → open → half_open → healthy

Thresholds and cool-down are configurable (ASSUMPTION (confirm) defaults):
  failure_threshold:  5 consecutive failures → open
  cool_down_seconds:  60s in open → half_open
  probe_on_half_open: one probe attempt; success → healthy, failure → open

State is stored per-connector, per-process.  For multi-worker deployments,
back this with Redis (future enhancement; the interface is the same).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from cam.connectors.errors import ConnectorError

HealthState = Literal["healthy", "degraded", "open", "half_open"]


@dataclass
class ConnectorHealth:
    """Per-connector circuit-breaker state machine."""

    connector: str
    failure_threshold: int = 5
    cool_down_seconds: float = 60.0

    _state: HealthState = field(default="healthy", init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _last_error: ConnectorError | None = field(default=None, init=False)

    # ------------------------------------------------------------------
    # Public read
    # ------------------------------------------------------------------

    @property
    def state(self) -> HealthState:
        self._maybe_transition_to_half_open()
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    @property
    def last_error(self) -> ConnectorError | None:
        return self._last_error

    # ------------------------------------------------------------------
    # Feedback from the OutboundClient
    # ------------------------------------------------------------------

    def record_success(self) -> None:
        """Call after a successful vendor response."""
        prev = self._state
        self._consecutive_failures = 0
        self._last_error = None
        if self._state in ("half_open", "open", "degraded"):
            self._state = "healthy"
            self._opened_at = None
        if prev != self._state:
            self._emit_transition(prev, self._state)

    def record_failure(self, error: ConnectorError) -> None:
        """Call after a classified ConnectorError."""
        self._last_error = error
        self._consecutive_failures += 1
        prev = self._state

        if self._state == "half_open":
            self._state = "open"
            self._opened_at = time.monotonic()
        elif self._state in ("healthy", "degraded"):
            if self._consecutive_failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()
            elif self._consecutive_failures >= max(1, self.failure_threshold // 2):
                self._state = "degraded"

        if prev != self._state:
            self._emit_transition(prev, self._state)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _maybe_transition_to_half_open(self) -> None:
        if (
            self._state == "open"
            and self._opened_at is not None
            and (time.monotonic() - self._opened_at) >= self.cool_down_seconds
        ):
            self._state = "half_open"
            self._emit_transition("open", "half_open")

    def _emit_transition(self, from_state: HealthState, to_state: HealthState) -> None:
        import structlog

        log = structlog.get_logger(__name__)
        log.info(
            "connector.circuit_transition",
            connector=self.connector,
            from_state=from_state,
            to_state=to_state,
        )
        _emit_circuit_metric(self.connector, to_state)
        if to_state == "open":
            log.warning(
                "connector.circuit_open",
                connector=self.connector,
                consecutive_failures=self._consecutive_failures,
            )


# ---------------------------------------------------------------------------
# Registry of per-connector health instances (one per process)
# ---------------------------------------------------------------------------

_HEALTH: dict[str, ConnectorHealth] = {}


def get_health(
    connector: str,
    failure_threshold: int = 5,
    cool_down_seconds: float = 60.0,
) -> ConnectorHealth:
    if connector not in _HEALTH:
        _HEALTH[connector] = ConnectorHealth(
            connector=connector,
            failure_threshold=failure_threshold,
            cool_down_seconds=cool_down_seconds,
        )
    return _HEALTH[connector]


def reset_health(connector: str) -> None:
    """Reset circuit state (test helper)."""
    _HEALTH.pop(connector, None)


# ---------------------------------------------------------------------------
# Prometheus metric (emitted on transition)
# ---------------------------------------------------------------------------

_circuit_gauge: Any = None


def _emit_circuit_metric(connector: str, state: HealthState) -> None:
    global _circuit_gauge
    try:
        from prometheus_client import Gauge

        if _circuit_gauge is None:
            _circuit_gauge = Gauge(
                "connector_circuit_state",
                "Circuit breaker state (0=healthy,1=degraded,2=open,3=half_open)",
                ["connector"],
            )
        state_map = {"healthy": 0, "degraded": 1, "open": 2, "half_open": 3}
        _circuit_gauge.labels(connector=connector).set(state_map.get(state, -1))
    except Exception as _exc:
        import structlog as _sl
        _sl.get_logger(__name__).debug("metric_init_failed", error=str(_exc))
