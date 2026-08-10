"""G10 — Crash-recovery sweep focused tests."""

from __future__ import annotations

import pytest

from cam.core.orchestrator.dsl import GateConfig, StepContext, clear_registry, workflow
from cam.core.orchestrator.engine import WorkflowEngine
from cam.core.orchestrator.idempotency import InMemoryIdempotencyStore
from cam.core.orchestrator.recovery import recovery_sweep
from cam.core.orchestrator.states import RunStatus
from cam.core.orchestrator.store import InMemoryRunStore
from cam.core.orchestrator.triggers import make_agent_trigger, start_run

KEY = b"test-signing-key-32-bytes-padded"


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_registry()


# a. Queued run is re-enqueued by recovery sweep
async def test_recovery_re_enqueues_queued_run() -> None:
    @workflow("rec_wf", version=1)
    class RecWF:
        steps = ["step_a"]
        async def step_a(self, ctx: StepContext) -> dict:
            return {"done": True}

    store = InMemoryRunStore()
    engine = WorkflowEngine(store=store, idem_store=InMemoryIdempotencyStore(), signing_key=KEY)
    run = await start_run("rec_wf", {}, make_agent_trigger("t"), store)
    assert run.status == RunStatus.QUEUED

    re_enqueued = await recovery_sweep(store, engine)
    assert run.id in re_enqueued

    recovered = await store.get_run(run.id)
    assert recovered.status == RunStatus.SUCCEEDED


# b. No step re-applied — step status check (SUCCEEDED) prevents re-execution
async def test_recovery_no_double_effect() -> None:
    call_count = 0

    @workflow("idem_rec", version=1)
    class IdemRecWF:
        steps = ["step_a"]

        async def step_a(self, ctx: StepContext) -> dict:
            nonlocal call_count
            call_count += 1
            return {"count": call_count}

    store = InMemoryRunStore()
    idem = InMemoryIdempotencyStore()
    engine = WorkflowEngine(store=store, idem_store=idem, signing_key=KEY)
    run = await start_run("idem_rec", {}, make_agent_trigger("t"), store)

    # First execution completes successfully
    result = await engine.execute(run.id)
    assert result.status == RunStatus.SUCCEEDED
    assert call_count == 1

    # Manually reset run to queued (simulating crash before the run status was persisted)
    # Step states remain SUCCEEDED — the engine skips them on re-entry
    store._runs[run.id] = store._runs[run.id].model_copy(update={"status": RunStatus.QUEUED})

    # Recovery sweep — engine skips SUCCEEDED steps, doesn't re-call handler
    await recovery_sweep(store, engine)
    assert call_count == 1  # handler not called a second time
    final = await store.get_run(run.id)
    assert final.status == RunStatus.SUCCEEDED


# c. Awaiting_approval run stays parked across restart
async def test_gated_run_stays_parked_on_recovery() -> None:
    @workflow("gated_rec", version=1)
    class GatedRecWF:
        steps = ["step_a", "GATE:approve", "step_b"]
        gates = {"approve": GateConfig(required_role="attorney", channels=["mcp"])}

        async def step_a(self, ctx: StepContext) -> dict: return {}
        async def step_b(self, ctx: StepContext) -> dict: return {}

    store = InMemoryRunStore()
    engine = WorkflowEngine(store=store, idem_store=InMemoryIdempotencyStore(), signing_key=KEY)
    run = await start_run("gated_rec", {}, make_agent_trigger("t"), store)
    await engine.execute(run.id)

    run_state = await store.get_run(run.id)
    assert run_state.status == RunStatus.AWAITING_APPROVAL

    re_enqueued = await recovery_sweep(store, engine)
    assert run.id not in re_enqueued  # gated run left alone

    still_awaiting = await store.get_run(run.id)
    assert still_awaiting.status == RunStatus.AWAITING_APPROVAL
