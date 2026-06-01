"""Crash-recovery sweep — spec §5.7, G10.

On startup, find runs in queued/running state and re-enqueue them.
Runs in awaiting_approval stay parked at their gate (no work to redo).
Because every completed step recorded its idem-key + output, re-entry
never re-applies an external effect (resume invariant).
"""

from __future__ import annotations

from typing import Any

import structlog

from cam.core.orchestrator.metrics import record_resume_recovery
from cam.core.orchestrator.states import RunStatus

log = structlog.get_logger(__name__)


async def recovery_sweep(
    store: Any,
    engine: Any,
    enqueue_fn: Any | None = None,
) -> list[str]:
    """Re-enqueue recoverable runs after a crash.

    Args:
        store:      RunStore.
        engine:     WorkflowEngine — used for in-process recovery (tests).
        enqueue_fn: Optional async callable(run_id) for Celery/queue-based execution.
                    If None, the engine executes runs directly (in-process).

    Returns:
        List of run_ids that were re-enqueued.
    """
    recoverable = await store.find_recoverable_runs()
    re_enqueued: list[str] = []

    for run_id in recoverable:
        run = await store.get_run(run_id)
        if run is None:
            continue
        if run.status == RunStatus.AWAITING_APPROVAL:
            log.info("recovery.gate_parked", run_id=run_id)
            continue

        log.info("recovery.re_enqueue", run_id=run_id, status=run.status)
        record_resume_recovery()
        re_enqueued.append(run_id)

        if enqueue_fn is not None:
            await enqueue_fn(run_id)
        else:
            # In-process: execute directly (for tests and local dev)
            try:
                await engine.execute(run_id)
            except Exception as exc:
                log.error("recovery.execute_failed", run_id=run_id, error=str(exc))

    log.info("recovery.sweep_complete", re_enqueued=len(re_enqueued))
    return re_enqueued
