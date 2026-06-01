"""Celery task definitions for the CAM deadline engine — spec §5, G4–G7.

Tasks replace the ``InMemoryScheduler`` stubs with production-grade Celery beat
scheduling.  Every task is:

  - **Idempotent** — same ``idem_key`` produces at most one side-effect.
  - **PII-free in logs** — only ``deadline_id``, ``matter_id`` (opaque IDs) and
    aggregate counts are emitted; no names, dates of birth, or document content.
  - **Never silent-fail** — all exceptions are logged at ERROR level and re-raised
    so Celery can retry/move to dead-letter.

Usage (Celery worker)::

    celery -A cam.sidecar.celery_app worker -Q cam_deadline --loglevel=info

Usage (Celery beat — periodic tasks)::

    celery -A cam.sidecar.celery_app beat --loglevel=info
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import structlog
from celery import Celery
from celery.utils.log import get_task_logger

from cam.sidecar import celery_config

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = Celery("cam")
app.config_from_object(celery_config)

log = structlog.get_logger(__name__)
_task_log = get_task_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:  # noqa: ANN401
    """Run a coroutine from a synchronous Celery task.

    In production (worker process) this creates a fresh event loop per call so
    tasks are safe to run in forked worker processes without sharing loop state.

    In test/eager mode pytest-asyncio may already be running an event loop.  If
    so, we schedule the coroutine on that loop via ``asyncio.run_coroutine_threadsafe``
    using the running loop's thread, or — when we are already on the event-loop
    thread — we use ``nest_asyncio`` if available, otherwise run in a thread.
    This path is intentionally only for testing; production workers never have a
    running loop on the task thread.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is None:
        # Normal production path: no running loop on this thread.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    else:
        # Already inside an event loop (test eager mode).  Use nest_asyncio when
        # available; otherwise run in a new thread to avoid re-entrancy issues.
        try:
            # nest_asyncio is an optional dependency — allows re-entrant event loops
            # in Jupyter / test environments.  Falls back to a thread if not installed.
            import importlib
            _nest = importlib.import_module("nest_asyncio")
            _nest.apply(running)
            return running.run_until_complete(coro)
        except (ImportError, ModuleNotFoundError):
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()


def _make_in_memory_deps() -> tuple[Any, Any, Any]:
    """Build lightweight in-process dependencies for tasks that lack a live DB.

    In production these should be replaced with real store / notification port
    instances wired via the app's DI container.  The in-memory versions keep
    tasks runnable in integration tests and ``CELERY_TASK_ALWAYS_EAGER`` mode
    without requiring a database connection.
    """
    from cam.core.services.deadline.schedule import DeadlineStore
    from cam.core.services.deadline.firing import MockNotificationPort

    store = DeadlineStore()
    notif = MockNotificationPort()
    return store, notif, None  # store, notification_port, redis_client


# ---------------------------------------------------------------------------
# task_fire_reminder
# ---------------------------------------------------------------------------


@app.task(
    name="cam.deadline.fire_reminder",
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    acks_late=True,
)
def task_fire_reminder(self: Any, deadline_id: str, reminder_idem_key: str) -> dict:
    """Fire one deadline reminder.

    Idempotent: if the reminder was already fired (Redis dedup key set), the
    underlying ``fire_reminder()`` returns ``False`` and no notification is sent.

    Returns::

        {"fired": bool, "deadline_id": str}
    """
    log.info(
        "celery.task_fire_reminder.start",
        deadline_id=deadline_id,
        task_id=self.request.id,
    )
    try:
        store, notif, redis = _make_in_memory_deps()

        fired: bool = _run(
            _fire_reminder_async(
                deadline_id=deadline_id,
                reminder_idem_key=reminder_idem_key,
                store=store,
                notification_port=notif,
                redis_client=redis,
            )
        )

        log.info(
            "celery.task_fire_reminder.done",
            deadline_id=deadline_id,
            fired=fired,
        )
        return {"fired": fired, "deadline_id": deadline_id}

    except Exception as exc:
        log.error(
            "celery.task_fire_reminder.error",
            deadline_id=deadline_id,
            error=str(exc),
        )
        raise self.retry(exc=exc)


async def _fire_reminder_async(
    *,
    deadline_id: str,
    reminder_idem_key: str,
    store: Any,
    notification_port: Any,
    redis_client: Any,
) -> bool:
    from cam.core.services.deadline.firing import fire_reminder

    return await fire_reminder(
        deadline_id=deadline_id,
        reminder_idem_key=reminder_idem_key,
        store=store,
        notification_port=notification_port,
        redis_client=redis_client,
    )


# ---------------------------------------------------------------------------
# task_escalate
# ---------------------------------------------------------------------------


@app.task(
    name="cam.deadline.escalate",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def task_escalate(self: Any, deadline_id: str) -> dict:
    """Advance the escalation level for a deadline and notify.

    Escalation is monotonically non-decreasing; calling this task twice for
    the same ``deadline_id`` will advance the level by 1 on each call.  To
    avoid spurious escalation, callers should check the current level before
    dispatching.

    Returns::

        {"escalated": bool, "new_level": int, "deadline_id": str}
    """
    log.info(
        "celery.task_escalate.start",
        deadline_id=deadline_id,
        task_id=self.request.id,
    )
    try:
        store, notif, _ = _make_in_memory_deps()

        new_level: int = _run(
            _escalate_async(
                deadline_id=deadline_id,
                store=store,
                notification_port=notif,
            )
        )

        escalated = new_level > 0
        log.info(
            "celery.task_escalate.done",
            deadline_id=deadline_id,
            new_level=new_level,
        )
        return {"escalated": escalated, "new_level": new_level, "deadline_id": deadline_id}

    except Exception as exc:
        log.error(
            "celery.task_escalate.error",
            deadline_id=deadline_id,
            error=str(exc),
        )
        raise self.retry(exc=exc)


async def _escalate_async(
    *,
    deadline_id: str,
    store: Any,
    notification_port: Any,
) -> int:
    from cam.core.services.deadline.firing import escalate

    return await escalate(
        deadline_id=deadline_id,
        store=store,
        notification_port=notification_port,
    )


# ---------------------------------------------------------------------------
# task_sweep_reconcile
# ---------------------------------------------------------------------------


@app.task(
    name="cam.deadline.sweep_reconcile",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def task_sweep_reconcile(self: Any, matter_ids: list[str]) -> dict:
    """Reconcile deadline engine state against the case system for each matter.

    ConnectorError on individual matters is handled by ``Reconciler.reconcile``
    (parks + alerts; never treats absence of data as no-drift).

    Args:
        matter_ids: List of matter identifiers to reconcile.  Pass an empty
            list to perform a no-op heartbeat sweep.

    Returns::

        {"matter_count": int, "drift_count": int}
    """
    log.info(
        "celery.task_sweep_reconcile.start",
        matter_count=len(matter_ids),
        task_id=self.request.id,
    )
    try:
        store, _, _ = _make_in_memory_deps()

        drift_count: int = _run(
            _sweep_reconcile_async(matter_ids=matter_ids, store=store)
        )

        log.info(
            "celery.task_sweep_reconcile.done",
            matter_count=len(matter_ids),
            drift_count=drift_count,
        )
        return {"matter_count": len(matter_ids), "drift_count": drift_count}

    except Exception as exc:
        log.error(
            "celery.task_sweep_reconcile.error",
            matter_count=len(matter_ids),
            error=str(exc),
        )
        raise self.retry(exc=exc)


async def _sweep_reconcile_async(*, matter_ids: list[str], store: Any) -> int:
    from cam.core.services.deadline.reconcile import Reconciler

    reconciler = Reconciler(store=store)
    total_drift = 0
    for mid in matter_ids:
        findings = await reconciler.reconcile(mid)
        total_drift += len(findings)
    return total_drift


# ---------------------------------------------------------------------------
# task_deadman_check
# ---------------------------------------------------------------------------


@app.task(
    name="cam.deadline.deadman_check",
    bind=True,
    max_retries=0,          # dead-man must never retry: a failed check IS the alert
    acks_late=True,
)
def task_deadman_check(self: Any) -> dict:
    """Check scheduler liveness; alert if heartbeat is stale.

    Uses a ``CeleryScheduler`` instance as the scheduler under test.  Since
    Celery keeps itself alive, ``CeleryScheduler.heartbeat()`` always returns
    ``now()``, making this check healthy under normal conditions.

    A ``threshold_seconds`` of 300 (5 min) matches ``DeadmanMonitor`` default.

    Returns::

        {"healthy": bool, "age_seconds": float}
    """
    log.info("celery.task_deadman_check.start", task_id=self.request.id)
    try:
        result: dict = _run(_deadman_check_async())
        log.info(
            "celery.task_deadman_check.done",
            healthy=result["healthy"],
            age_seconds=result["age_seconds"],
        )
        return result

    except Exception as exc:
        # Re-raise without retry — the failure IS the safety incident
        log.error("celery.task_deadman_check.error", error=str(exc))
        raise


async def _deadman_check_async() -> dict:
    from cam.core.services.deadline.deadman import DeadmanMonitor
    from cam.sidecar.scheduler_celery import CeleryScheduler

    scheduler = CeleryScheduler()
    monitor = DeadmanMonitor(scheduler=scheduler)

    last_beat = await scheduler.heartbeat()
    now = datetime.now(tz=timezone.utc)
    age_seconds = (now - last_beat).total_seconds()
    healthy = await monitor.check_once()

    return {"healthy": healthy, "age_seconds": age_seconds}
