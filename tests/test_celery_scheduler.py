"""Tests for CeleryScheduler and Celery deadline tasks — no broker required.

All tests run in eager mode (``CELERY_TASK_ALWAYS_EAGER=True`` via the
``celery_eager`` fixture) so no running Redis or Celery worker is needed.

Coverage:
  1. ``CeleryScheduler.heartbeat()`` returns a recent UTC datetime.
  2. ``CeleryScheduler.arm()`` returns a non-empty string arm_id.
  3. ``task_deadman_check.apply()`` runs eagerly and returns a healthy result.
  4. ``task_sweep_reconcile.apply()`` runs eagerly with an empty matter list.
  5. ``task_fire_reminder.apply()`` runs eagerly (no deadline in store → fired=False).
  6. ``CeleryScheduler`` satisfies the ``Scheduler`` protocol (isinstance check).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# Ensure Celery runs tasks eagerly (no broker needed)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def celery_eager(monkeypatch: pytest.MonkeyPatch):
    """Force Celery into eager (synchronous) mode for all tests in this file."""
    # Set env vars before the app is imported so config picks them up
    monkeypatch.setenv("CAM_REDIS_URL", "redis://localhost:6379/0")

    # Import after env is patched
    from cam.sidecar.celery_app import app  # noqa: PLC0415

    app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
    )
    yield
    # Reset — other test files may import the app and should not inherit eager mode
    app.conf.update(
        task_always_eager=False,
        task_eager_propagates=False,
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# 1. heartbeat() returns a recent datetime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_returns_recent_datetime():
    from cam.sidecar.scheduler_celery import CeleryScheduler  # noqa: PLC0415

    sched = CeleryScheduler()
    before = datetime.now(tz=UTC)
    beat = await sched.heartbeat()
    after = datetime.now(tz=UTC)

    assert isinstance(beat, datetime)
    assert beat.tzinfo is not None, "heartbeat must be timezone-aware"
    assert before <= beat <= after, "heartbeat must be within the test window"


# ---------------------------------------------------------------------------
# 2. arm() returns a non-empty string arm_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arm_returns_non_empty_arm_id():
    from cam.sidecar.scheduler_celery import CeleryScheduler  # noqa: PLC0415

    sched = CeleryScheduler()
    fire_at = datetime.now(tz=UTC) + timedelta(hours=1)
    arm_id = await sched.arm(
        fire_at=fire_at,
        job_ref="deadline_reminder:test-deadline-001",
        idem_key="test-idem-001",
    )

    assert isinstance(arm_id, str)
    assert len(arm_id) > 0, "arm_id must be non-empty"


# ---------------------------------------------------------------------------
# 3. task_deadman_check runs eagerly and returns healthy
# ---------------------------------------------------------------------------


def test_task_deadman_check_eager_healthy():
    """task_deadman_check should run eagerly and report healthy=True.

    Since CeleryScheduler.heartbeat() returns datetime.now(), the age will
    always be near-zero and well below the 300 s staleness threshold.
    """
    from cam.sidecar.celery_app import task_deadman_check  # noqa: PLC0415

    result = task_deadman_check.apply().get()

    assert isinstance(result, dict)
    assert "healthy" in result
    assert "age_seconds" in result
    assert result["healthy"] is True, f"Expected healthy=True, got {result}"
    assert isinstance(result["age_seconds"], float)
    assert result["age_seconds"] >= 0.0


# ---------------------------------------------------------------------------
# 4. task_sweep_reconcile runs eagerly with empty matter list
# ---------------------------------------------------------------------------


def test_task_sweep_reconcile_empty_list():
    from cam.sidecar.celery_app import task_sweep_reconcile  # noqa: PLC0415

    result = task_sweep_reconcile.apply(kwargs={"matter_ids": []}).get()

    assert isinstance(result, dict)
    assert result["matter_count"] == 0
    assert result["drift_count"] == 0


# ---------------------------------------------------------------------------
# 5. task_fire_reminder runs eagerly — no deadline in store → fired=False
# ---------------------------------------------------------------------------


def test_task_fire_reminder_no_deadline():
    """When no deadline exists in the in-memory store, fired should be False."""
    from cam.sidecar.celery_app import task_fire_reminder  # noqa: PLC0415

    result = task_fire_reminder.apply(
        kwargs={
            "deadline_id": "nonexistent-deadline-id",
            "reminder_idem_key": "test-reminder-idem-001",
        }
    ).get()

    assert isinstance(result, dict)
    assert result["deadline_id"] == "nonexistent-deadline-id"
    assert result["fired"] is False


# ---------------------------------------------------------------------------
# 6. CeleryScheduler satisfies the Scheduler protocol
# ---------------------------------------------------------------------------


def test_celery_scheduler_satisfies_protocol():
    from cam.core.services.deadline.schedule import Scheduler  # noqa: PLC0415
    from cam.sidecar.scheduler_celery import CeleryScheduler  # noqa: PLC0415

    sched = CeleryScheduler()
    assert isinstance(sched, Scheduler), (
        "CeleryScheduler must satisfy the Scheduler runtime_checkable protocol"
    )
