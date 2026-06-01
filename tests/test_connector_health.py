"""G5 — ConnectorHealth circuit-breaker tests."""

from __future__ import annotations

import time

import pytest

from cam.connectors.errors import AuthError, TransientError
from cam.connectors.health import ConnectorHealth, reset_health


def make_health(threshold: int = 3, cool_down: float = 0.05) -> ConnectorHealth:
    reset_health("test_circuit")
    return ConnectorHealth("test_circuit", failure_threshold=threshold, cool_down_seconds=cool_down)


def transient() -> TransientError:
    return TransientError("test_circuit", "timeout")


# a. N failures trip open
def test_trip_open_after_threshold() -> None:
    h = make_health(threshold=3)
    assert h.state == "healthy"
    for _ in range(3):
        h.record_failure(transient())
    assert h.state == "open"


# b. open → fail-fast (is_open == True)
def test_open_is_open() -> None:
    h = make_health(threshold=1)
    h.record_failure(transient())
    assert h.is_open is True


# c. cool-down → half-open
def test_cool_down_transitions_to_half_open() -> None:
    h = make_health(threshold=1, cool_down=0.01)
    h.record_failure(transient())
    assert h.state == "open"
    time.sleep(0.02)
    assert h.state == "half_open"


# d. probe success closes
def test_half_open_probe_success_closes() -> None:
    h = make_health(threshold=1, cool_down=0.01)
    h.record_failure(transient())
    time.sleep(0.02)
    assert h.state == "half_open"
    h.record_success()
    assert h.state == "healthy"


# e. probe failure re-opens
def test_half_open_probe_failure_reopens() -> None:
    h = make_health(threshold=1, cool_down=0.01)
    h.record_failure(transient())
    time.sleep(0.02)
    assert h.state == "half_open"
    h.record_failure(transient())
    assert h.state == "open"


# f. isolation — one connector's state doesn't affect another
def test_connector_isolation() -> None:
    h1 = ConnectorHealth("connector_alpha", failure_threshold=1)
    h2 = ConnectorHealth("connector_beta", failure_threshold=5)
    h1.record_failure(transient())
    assert h1.is_open
    assert not h2.is_open


# g. success resets failure counter
def test_success_resets_counter() -> None:
    h = make_health(threshold=3)
    h.record_failure(transient())
    h.record_failure(transient())
    h.record_success()
    # After success, counter resets; two more failures shouldn't trip open again
    h.record_failure(transient())
    h.record_failure(transient())
    assert h.state != "open"
