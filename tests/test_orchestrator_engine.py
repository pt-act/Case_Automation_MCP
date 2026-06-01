"""G3 + G5 + G6 — Engine, retry/compensation, triggers focused tests."""

from __future__ import annotations

import pytest

from cam.core.orchestrator.dsl import GateConfig, StepContext, clear_registry, workflow
from cam.core.orchestrator.engine import WorkflowEngine
from cam.core.orchestrator.idempotency import InMemoryIdempotencyStore
from cam.core.orchestrator.states import RunStatus, StepStatus
from cam.core.orchestrator.store import InMemoryRunStore
from cam.core.orchestrator.triggers import make_agent_trigger, start_run
from cam.connectors.errors import FatalError, TransientError


SIGNING_KEY = b"test-signing-key-32-bytes-padded"


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_registry()


def make_engine(store=None, idem=None):
    store = store or InMemoryRunStore()
    idem = idem or InMemoryIdempotencyStore()
    return WorkflowEngine(store=store, idem_store=idem, signing_key=SIGNING_KEY), store


# a. Two-step run completes in order
async def test_two_step_run_completes() -> None:
    order = []

    @workflow("simple", version=1)
    class SimpleWF:
        steps = ["step_a", "step_b"]

        async def step_a(self, ctx: StepContext) -> dict:
            order.append("a")
            return {"a": 1}

        async def step_b(self, ctx: StepContext) -> dict:
            order.append("b")
            return {"b": 2}

    store = InMemoryRunStore()
    engine, _ = make_engine(store)
    trigger = make_agent_trigger("test_agent")
    run = await start_run("simple", {}, trigger, store)
    result = await engine.execute(run.id)

    assert result.status == RunStatus.SUCCEEDED
    assert order == ["a", "b"]


# c. Handler reads prior step output
async def test_handler_reads_prior_output() -> None:
    received = {}

    @workflow("chain", version=1)
    class ChainWF:
        steps = ["step_a", "step_b"]

        async def step_a(self, ctx: StepContext) -> dict:
            return {"value": 42}

        async def step_b(self, ctx: StepContext) -> dict:
            received["from_a"] = ctx.output("step_a")
            return {}

    store = InMemoryRunStore()
    engine, _ = make_engine(store)
    run = await start_run("chain", {}, make_agent_trigger("t"), store)
    await engine.execute(run.id)
    assert received["from_a"] == {"value": 42}


# d. Compensation hook registered + called on park
async def test_compensation_called_on_park() -> None:
    compensated = []

    @workflow("comp", version=1)
    class CompWF:
        steps = ["step_a", "step_b"]

        async def step_a(self, ctx: StepContext) -> dict:
            ctx.compensate(lambda: compensated.append("a_reversed"))
            return {"a": 1}

        async def step_b(self, ctx: StepContext) -> dict:
            raise FatalError("comp_test", "forced fatal")

    store = InMemoryRunStore()
    engine, _ = make_engine(store)
    run = await start_run("comp", {}, make_agent_trigger("t"), store)
    result = await engine.execute(run.id)

    assert result.status == RunStatus.PARKED
    assert "a_reversed" in compensated


# e. Worker stops at GATE:* in awaiting_approval
async def test_worker_stops_at_gate() -> None:
    @workflow("gated", version=1)
    class GatedWF:
        steps = ["step_a", "GATE:human_review", "step_b"]
        gates = {"human_review": GateConfig(required_role="attorney", channels=["mcp"])}

        async def step_a(self, ctx: StepContext) -> dict:
            return {"ok": True}

        async def step_b(self, ctx: StepContext) -> dict:
            return {}

    store = InMemoryRunStore()
    engine, _ = make_engine(store)
    run = await start_run("gated", {}, make_agent_trigger("t"), store)
    result = await engine.execute(run.id)

    assert result.status == RunStatus.AWAITING_APPROVAL


# Triggers: agent start creates a run
async def test_agent_trigger_creates_run() -> None:
    @workflow("trigger_wf", version=1)
    class TriggerWF:
        steps = ["step_a"]
        async def step_a(self, ctx: StepContext) -> dict: return {}

    store = InMemoryRunStore()
    run = await start_run("trigger_wf", {}, make_agent_trigger("agent_x"), store)
    assert run.id is not None
    assert run.status == RunStatus.QUEUED


# Triggers: duplicate event id → one run
async def test_duplicate_event_single_run() -> None:
    @workflow("dedup_wf", version=1)
    class DedupWF:
        steps = ["step_a"]
        async def step_a(self, ctx: StepContext) -> dict: return {}

    store = InMemoryRunStore()
    from cam.core.orchestrator.triggers import make_event_trigger
    t = make_event_trigger("evt-999")

    run1 = await start_run("dedup_wf", {}, t, store)
    run2 = await start_run("dedup_wf", {}, t, store)
    assert run1.id == run2.id


# Triggers: unknown workflow rejected
async def test_unknown_workflow_rejected() -> None:
    store = InMemoryRunStore()
    with pytest.raises(KeyError):
        await start_run("no_such_workflow", {}, make_agent_trigger("x"), store)


# Idempotency: re-run of a recorded step performs no external effect
async def test_idempotency_short_circuit() -> None:
    call_count = 0

    @workflow("idem_wf", version=1)
    class IdemWF:
        steps = ["step_a"]

        async def step_a(self, ctx: StepContext) -> dict:
            nonlocal call_count
            call_count += 1
            return {"n": call_count}

    store = InMemoryRunStore()
    idem = InMemoryIdempotencyStore()
    engine = WorkflowEngine(store=store, idem_store=idem, signing_key=SIGNING_KEY)

    run = await start_run("idem_wf", {}, make_agent_trigger("t"), store)
    await engine.execute(run.id)
    assert call_count == 1

    # Pre-seed the idempotency store with the step's key to simulate a recorded step
    run2 = await start_run("idem_wf", {}, make_agent_trigger("t2"), store)
    step_key = f"{run2.id}:step_a"
    idem._force_record(step_key, {"n": 99})

    await engine.execute(run2.id)
    assert call_count == 1  # handler not called again
