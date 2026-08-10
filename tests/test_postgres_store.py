"""PostgresRunStore tests — requires live Postgres via CAM_DATABASE_URL.

Same pattern as test_persistence.py: uses the db_engine/db_session fixtures
from conftest.py to get a fresh schema per test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from cam.core.orchestrator.postgres_store import PostgresRunStore
from cam.core.orchestrator.states import (
    ApprovalDecision,
    ApprovalToken,
    GateRequest,
    RunStatus,
    StepState,
    StepStatus,
    TriggerRef,
    WorkflowRun,
)
from cam.core.orchestrator.store import IllegalTransitionError


def _make_run(
    run_id: str | None = None,
    dedupe_key: str | None = None,
) -> WorkflowRun:
    now = datetime.now(tz=UTC)
    return WorkflowRun(
        id=run_id or str(uuid.uuid4()),
        workflow="test_wf",
        workflow_version=1,
        status=RunStatus.QUEUED,
        trigger=TriggerRef(kind="event", source="evt-1", dedupe_key=dedupe_key),
        context={"case_id": "C-100"},
        created_at=now,
        updated_at=now,
    )


def _make_steps(run_id: str) -> list[StepState]:
    return [
        StepState(run_id=run_id, step="step_a", seq=0, idem_key=f"{run_id}:step_a"),
        StepState(run_id=run_id, step="step_b", seq=1, idem_key=f"{run_id}:step_b"),
    ]


@pytest.fixture()
async def store(db_engine):
    """PostgresRunStore with a session factory bound to the test engine."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    return PostgresRunStore(factory)


# -- create + get ----------------------------------------------------------

async def test_create_and_get_run(store, db_engine):
    """A run created via PostgresRunStore can be retrieved with all fields intact."""
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    steps = _make_steps(run.id)
    created = await store.create_run(run, steps)
    assert created.id == run.id

    fetched = await store.get_run(run.id)
    assert fetched is not None
    assert fetched.id == run.id
    assert fetched.workflow == "test_wf"
    assert fetched.status == RunStatus.QUEUED
    assert fetched.trigger.kind == "event"
    assert fetched.trigger.dedupe_key is None
    assert fetched.context == {"case_id": "C-100"}


async def test_get_run_not_found(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    assert await store.get_run("nonexistent") is None


async def test_create_run_dedupe_returns_existing(store, db_engine):
    """Creating a run with a duplicate dedupe_key returns the existing run."""
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run1 = _make_run(dedupe_key="dedup-1")
    steps1 = _make_steps(run1.id)
    await store.create_run(run1, steps1)

    run2 = _make_run(dedupe_key="dedup-1")
    steps2 = _make_steps(run2.id)
    result = await store.create_run(run2, steps2)
    assert result.id == run1.id  # got back the first run


# -- steps -----------------------------------------------------------------

async def test_get_steps(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    steps = _make_steps(run.id)
    await store.create_run(run, steps)

    fetched = await store.get_steps(run.id)
    assert len(fetched) == 2
    assert fetched[0].step == "step_a"
    assert fetched[0].seq == 0
    assert fetched[1].step == "step_b"
    assert fetched[1].seq == 1


async def test_get_step_not_found(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    assert await store.get_step("nonexistent", "step_a") is None


async def test_update_step(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    steps = _make_steps(run.id)
    await store.create_run(run, steps)

    # Transition step_a: PENDING → RUNNING → SUCCEEDED
    step = await store.get_step(run.id, "step_a")
    assert step is not None
    now = datetime.now(tz=UTC)
    updated = step.model_copy(update={
        "status": StepStatus.RUNNING,
        "started_at": now,
    })
    await store.update_step(updated)

    fetched = await store.get_step(run.id, "step_a")
    assert fetched is not None
    assert fetched.status == StepStatus.RUNNING
    assert fetched.started_at == now


async def test_update_step_illegal_transition(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    steps = _make_steps(run.id)
    await store.create_run(run, steps)

    step = await store.get_step(run.id, "step_a")
    assert step is not None
    # PENDING → SUCCEEDED is illegal (must go through RUNNING)
    illegal = step.model_copy(update={"status": StepStatus.SUCCEEDED})
    with pytest.raises(IllegalTransitionError):
        await store.update_step(illegal)


# -- run status transitions -----------------------------------------------

async def test_update_run_status(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    await store.create_run(run, _make_steps(run.id))

    await store.update_run_status(run.id, RunStatus.RUNNING)
    fetched = await store.get_run(run.id)
    assert fetched is not None
    assert fetched.status == RunStatus.RUNNING


async def test_update_run_status_illegal(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    await store.create_run(run, _make_steps(run.id))

    # QUEUED → SUCCEEDED is illegal
    with pytest.raises(IllegalTransitionError):
        await store.update_run_status(run.id, RunStatus.SUCCEEDED)


async def test_set_current_step(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    await store.create_run(run, _make_steps(run.id))
    await store.update_run_status(run.id, RunStatus.RUNNING)
    await store.set_current_step(run.id, "step_b")

    fetched = await store.get_run(run.id)
    assert fetched is not None
    assert fetched.current_step == "step_b"


async def test_advance_gate_step(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    steps = [
        StepState(run_id=run.id, step="GATE:approval", seq=0, idem_key=f"{run.id}:GATE:approval"),
    ]
    await store.create_run(run, steps)

    await store.advance_gate_step(run.id, "GATE:approval")
    step = await store.get_step(run.id, "GATE:approval")
    assert step is not None
    assert step.status == StepStatus.SUCCEEDED
    assert step.ended_at is not None


# -- find runs -------------------------------------------------------------

async def test_find_queued_runs(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run1 = _make_run()
    run2 = _make_run()
    await store.create_run(run1, _make_steps(run1.id))
    await store.create_run(run2, _make_steps(run2.id))

    # Move run1 to RUNNING
    await store.update_run_status(run1.id, RunStatus.RUNNING)

    queued = await store.find_queued_runs()
    assert run2.id in queued
    assert run1.id not in queued


async def test_find_recoverable_runs(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run1 = _make_run()
    run2 = _make_run()
    run3 = _make_run()
    await store.create_run(run1, _make_steps(run1.id))
    await store.create_run(run2, _make_steps(run2.id))
    await store.create_run(run3, _make_steps(run3.id))

    await store.update_run_status(run1.id, RunStatus.RUNNING)
    await store.update_run_status(run2.id, RunStatus.RUNNING)
    await store.update_run_status(run2.id, RunStatus.SUCCEEDED)

    recoverable = await store.find_recoverable_runs()
    assert run1.id in recoverable  # RUNNING
    assert run2.id not in recoverable  # SUCCEEDED
    assert run3.id in recoverable  # QUEUED


# -- gates + tokens + decisions -------------------------------------------

async def test_create_and_get_gate_request(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    await store.create_run(run, _make_steps(run.id))

    now = datetime.now(tz=UTC)
    gate = GateRequest(
        id=str(uuid.uuid4()),
        run_id=run.id,
        step="GATE:approval",
        required_role="attorney",
        created_at=now,
        expires_at=now,
    )
    await store.create_gate_request(gate)

    fetched = await store.get_gate_request(gate.id)
    assert fetched is not None
    assert fetched.run_id == run.id
    assert fetched.required_role == "attorney"
    assert fetched.status == "pending"


async def test_update_gate_request(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    await store.create_run(run, _make_steps(run.id))

    now = datetime.now(tz=UTC)
    gate = GateRequest(
        id=str(uuid.uuid4()),
        run_id=run.id,
        step="GATE:approval",
        required_role="attorney",
        created_at=now,
        expires_at=now,
    )
    await store.create_gate_request(gate)

    updated = gate.model_copy(update={"status": "approved"})
    await store.update_gate_request(updated)

    fetched = await store.get_gate_request(gate.id)
    assert fetched is not None
    assert fetched.status == "approved"


async def test_save_and_get_token(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    await store.create_run(run, _make_steps(run.id))

    now = datetime.now(tz=UTC)
    gate = GateRequest(
        id="gate-token-test",
        run_id=run.id,
        step="GATE:approval",
        required_role="attorney",
        created_at=now,
        expires_at=now,
    )
    await store.create_gate_request(gate)

    token = ApprovalToken(
        id=str(uuid.uuid4()),
        gate_request_id="gate-token-test",
        run_id=run.id,
        step="GATE:approval",
        key_id="test-key",
        token_hash="a" * 64,
        channel="web",
        expires_at=now,
    )
    await store.save_token(token)

    fetched = await store.get_token(token.id)
    assert fetched is not None
    assert fetched.token_hash == "a" * 64
    assert not fetched.is_used


async def test_mark_token_used(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    await store.create_run(run, _make_steps(run.id))

    now = datetime.now(tz=UTC)
    gate = GateRequest(
        id="gate-used-test",
        run_id=run.id,
        step="GATE:approval",
        required_role="attorney",
        created_at=now,
        expires_at=now,
    )
    await store.create_gate_request(gate)

    token = ApprovalToken(
        id=str(uuid.uuid4()),
        gate_request_id="gate-used-test",
        run_id=run.id,
        step="GATE:approval",
        key_id="test-key",
        token_hash="a" * 64,
        channel="web",
        expires_at=now,
    )
    await store.save_token(token)

    used_at = datetime.now(tz=UTC)
    assert await store.mark_token_used(token.id, used_at) is True
    # Second use returns False
    assert await store.mark_token_used(token.id, used_at) is False

    fetched = await store.get_token(token.id)
    assert fetched is not None
    assert fetched.is_used


async def test_save_approval_decision(store, db_engine):
    from cam.persistence.models import Base
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    run = _make_run()
    await store.create_run(run, _make_steps(run.id))

    now = datetime.now(tz=UTC)
    gate = GateRequest(
        id="gate-decision-test",
        run_id=run.id,
        step="GATE:approval",
        required_role="attorney",
        created_at=now,
        expires_at=now,
    )
    await store.create_gate_request(gate)

    decision = ApprovalDecision(
        gate_request_id="gate-decision-test",
        run_id=run.id,
        step="GATE:approval",
        decision="approve",
        actor="attorney-1",
        channel="web",
        token_id="token-1",
        decided_at=datetime.now(tz=UTC),
    )
    # Should not raise
    await store.save_approval_decision(decision)
