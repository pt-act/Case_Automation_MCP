"""Trigger intake — start_run, event/schedule/agent bindings — spec §5.3, G6.

All three trigger paths (webhook event, schedule, agent) converge on
`start_run(workflow, context, trigger, idem_key)`.  Idempotent start via
`dedupe_key` ensures one run per event/schedule-tick.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from cam.core.orchestrator.dsl import GateConfig, get_workflow_latest
from cam.core.orchestrator.states import (
    RunStatus,
    StepState,
    StepStatus,
    TriggerRef,
    WorkflowRun,
)

log = structlog.get_logger(__name__)


async def start_run(
    workflow_name: str,
    context: dict[str, Any],
    trigger: TriggerRef,
    store: Any,
    idem_key: str | None = None,
) -> WorkflowRun:
    """Create and enqueue a new workflow run — idempotent on dedupe_key.

    If a run already exists for `trigger.dedupe_key` (or `idem_key`), the
    existing run is returned with no new side effects.

    Args:
        workflow_name: Registered workflow name (latest version used).
        context: Immutable run-scoped inputs validated against input_schema.
        trigger: How the run was triggered.
        store: RunStore (in-memory for tests, SQLAlchemy for prod).
        idem_key: Optional explicit dedupe key (overrides trigger.dedupe_key).
    """
    defn = get_workflow_latest(workflow_name)
    defn.validate_context(context)

    dedupe_key = idem_key or trigger.dedupe_key
    if dedupe_key:
        trigger = trigger.model_copy(update={"dedupe_key": dedupe_key})

    now = datetime.now(tz=timezone.utc)
    run_id = str(uuid.uuid4())

    run = WorkflowRun(
        id=run_id,
        workflow=workflow_name,
        workflow_version=defn.version,
        status=RunStatus.QUEUED,
        current_step=None,
        trigger=trigger,
        context=context,
        created_at=now,
        updated_at=now,
    )

    # Fan out one StepState per step
    step_states = [
        StepState(
            run_id=run_id,
            step=step_name,
            seq=idx,
            status=StepStatus.PENDING,
            idem_key=f"{run_id}:{step_name}",
        )
        for idx, step_name in enumerate(defn.steps)
    ]

    created = await store.create_run(run, step_states)
    if created.id != run_id:
        log.info("trigger.idempotent_start", workflow=workflow_name, existing_run=created.id)
    else:
        log.info("trigger.run_created", workflow=workflow_name, run_id=run_id, kind=trigger.kind)

    return created


def make_event_trigger(event_id: str, source: str = "webhook") -> TriggerRef:
    return TriggerRef(kind="event", source=source, dedupe_key=event_id)


def make_schedule_trigger(schedule_id: str) -> TriggerRef:
    return TriggerRef(kind="schedule", source=schedule_id, dedupe_key=None)


def make_agent_trigger(agent_identity: str, idem_key: str | None = None) -> TriggerRef:
    return TriggerRef(kind="agent", source=agent_identity, dedupe_key=idem_key)
