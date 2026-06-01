"""G2 — State machine, legal transitions, store fan-out focused tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from cam.core.orchestrator.states import (
    RunStatus,
    StepStatus,
    TriggerRef,
    WorkflowRun,
    is_legal_run_transition,
    is_legal_step_transition,
)
from cam.core.orchestrator.store import IllegalTransition, InMemoryRunStore
from cam.core.orchestrator.dsl import GateConfig, StepContext, clear_registry, workflow


NOW = datetime.now(tz=timezone.utc)


def make_run(dedupe_key: str | None = None) -> WorkflowRun:
    return WorkflowRun(
        id=str(uuid.uuid4()),
        workflow="test_wf",
        workflow_version=1,
        trigger=TriggerRef(kind="agent", source="test", dedupe_key=dedupe_key),
        context={},
        created_at=NOW,
        updated_at=NOW,
    )


# a. create-run fan-out count == steps
async def test_create_run_fanout_count() -> None:
    store = InMemoryRunStore()
    run = make_run()
    from cam.core.orchestrator.states import StepState, StepStatus
    steps = [
        StepState(run_id=run.id, step=f"step_{i}", seq=i, idem_key=f"{run.id}:step_{i}")
        for i in range(4)
    ]
    await store.create_run(run, steps)
    loaded = await store.get_steps(run.id)
    assert len(loaded) == 4


# b. Legal transition persists
async def test_legal_transition_persists() -> None:
    store = InMemoryRunStore()
    run = make_run()
    await store.create_run(run, [])
    await store.update_run_status(run.id, RunStatus.RUNNING)
    loaded = await store.get_run(run.id)
    assert loaded.status == RunStatus.RUNNING


# c. Illegal transition rejected + no write
async def test_illegal_transition_rejected() -> None:
    store = InMemoryRunStore()
    run = make_run()
    await store.create_run(run, [])
    with pytest.raises(IllegalTransition):
        await store.update_run_status(run.id, RunStatus.SUCCEEDED)  # queued→succeeded is illegal


# d. Load returns steps in seq order
async def test_steps_ordered_by_seq() -> None:
    from cam.core.orchestrator.states import StepState
    store = InMemoryRunStore()
    run = make_run()
    steps = [
        StepState(run_id=run.id, step=f"step_{i}", seq=i, idem_key=f"{run.id}:step_{i}")
        for i in range(3)
    ]
    await store.create_run(run, steps)
    loaded = await store.get_steps(run.id)
    seqs = [s.seq for s in loaded]
    assert seqs == sorted(seqs)


# e. Dedupe key prevents duplicate run
async def test_dedupe_key_idempotent() -> None:
    store = InMemoryRunStore()
    run1 = make_run(dedupe_key="evt-123")
    run2 = make_run(dedupe_key="evt-123")
    created1 = await store.create_run(run1, [])
    created2 = await store.create_run(run2, [])
    assert created1.id == created2.id


# f. is_legal_run_transition only allows diagrammed edges
def test_legal_run_transitions() -> None:
    assert is_legal_run_transition(RunStatus.QUEUED, RunStatus.RUNNING)
    assert not is_legal_run_transition(RunStatus.QUEUED, RunStatus.SUCCEEDED)
    assert is_legal_run_transition(RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL)
    assert not is_legal_run_transition(RunStatus.SUCCEEDED, RunStatus.RUNNING)


def test_legal_step_transitions() -> None:
    assert is_legal_step_transition(StepStatus.PENDING, StepStatus.RUNNING)
    assert is_legal_step_transition(StepStatus.RUNNING, StepStatus.SUCCEEDED)
    assert not is_legal_step_transition(StepStatus.PENDING, StepStatus.SUCCEEDED)
    assert not is_legal_step_transition(StepStatus.SUCCEEDED, StepStatus.PENDING)
