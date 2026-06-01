"""G7 + G8 — Gate core: token issuance, resolve_gate, channels focused tests."""

from __future__ import annotations

import pytest

from cam.core.orchestrator.dsl import GateConfig, StepContext, clear_registry, workflow
from cam.core.orchestrator.engine import WorkflowEngine
from cam.core.orchestrator.gates import (
    GateResolutionError,
    issue_token,
    resolve_gate,
    verify_token,
)
from cam.core.orchestrator.idempotency import InMemoryIdempotencyStore
from cam.core.orchestrator.states import RunStatus
from cam.core.orchestrator.store import InMemoryRunStore
from cam.core.orchestrator.triggers import make_agent_trigger, start_run


KEY = b"test-signing-key-32-bytes-padded"


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_registry()


async def _build_gated_run(wf_name: str):
    """Register a gated workflow under wf_name, run it to the gate, return (store, run_id, raw_token, token)."""

    class _GatedWF:
        steps = ["step_a", "GATE:approve", "step_b"]
        gates = {"approve": GateConfig(required_role="attorney", channels=["mcp"])}

        async def step_a(self, ctx: StepContext) -> dict:
            return {}

        async def step_b(self, ctx: StepContext) -> dict:
            return {}

    workflow(wf_name, version=1)(_GatedWF)

    store = InMemoryRunStore()
    engine = WorkflowEngine(store=store, idem_store=InMemoryIdempotencyStore(), signing_key=KEY)
    run = await start_run(wf_name, {}, make_agent_trigger("t"), store)
    await engine.execute(run.id)

    gate = list(store._gates.values())[0]
    raw_token, token_record = issue_token(
        gate_request_id=gate.id,
        run_id=run.id,
        step=gate.step,
        channel="mcp",
        signing_key=KEY,
    )
    await store.save_token(token_record)
    return store, run.id, raw_token, token_record


# a. Valid approve resumes run
async def test_valid_approve_resumes_run() -> None:
    store, run_id, raw_token, _ = await _build_gated_run("wf_approve")
    result = await resolve_gate(raw_token, "approve", "attorney", "mcp", KEY, store)
    assert result.decision == "approve"
    run = await store.get_run(run_id)
    assert run.status == RunStatus.RUNNING


# b. Reject → rejected, no post-gate
async def test_reject_terminates_run() -> None:
    store, run_id, raw_token, _ = await _build_gated_run("wf_reject")
    result = await resolve_gate(raw_token, "reject", "attorney", "mcp", KEY, store)
    assert result.decision == "reject"
    run = await store.get_run(run_id)
    assert run.status == RunStatus.REJECTED


# c. Expired token rejected
async def test_expired_token_rejected() -> None:
    store, run_id, _valid_raw, _ = await _build_gated_run("wf_expired")
    gate = list(store._gates.values())[0]
    expired_raw, expired_record = issue_token(
        gate_request_id=gate.id,
        run_id=run_id,
        step=gate.step,
        channel="mcp",
        signing_key=KEY,
        ttl_seconds=-1,
    )
    await store.save_token(expired_record)
    with pytest.raises(GateResolutionError, match="expired"):
        await resolve_gate(expired_raw, "approve", "attorney", "mcp", KEY, store)


# d. Reused token → error, no second resume
async def test_reused_token_rejected() -> None:
    store, run_id, raw_token, _ = await _build_gated_run("wf_reuse")
    await resolve_gate(raw_token, "approve", "attorney", "mcp", KEY, store)
    with pytest.raises(GateResolutionError, match="already been used"):
        await resolve_gate(raw_token, "approve", "attorney", "mcp", KEY, store)


# e. Wrong role → GateResolutionError (403)
async def test_wrong_role_rejected() -> None:
    store, run_id, raw_token, _ = await _build_gated_run("wf_wrongrole")
    with pytest.raises(GateResolutionError) as exc_info:
        await resolve_gate(raw_token, "approve", "intake_coordinator", "mcp", KEY, store)
    assert exc_info.value.status_code == 403


# f. Tampered token fails verify
async def test_tampered_token_rejected() -> None:
    store, run_id, raw_token, _ = await _build_gated_run("wf_tamper")
    tampered = raw_token[:-4] + "XXXX"
    with pytest.raises(GateResolutionError, match="tampered|verification"):
        await resolve_gate(tampered, "approve", "attorney", "mcp", KEY, store)


# g. Token verify: signature + payload roundtrip
def test_token_verify_roundtrip() -> None:
    raw_token, _ = issue_token("gid", "rid", "GATE:step", "mcp", KEY)
    payload, err = verify_token(raw_token, KEY)
    assert err is None
    assert payload["gid"] == "gid"
    assert payload["rid"] == "rid"


def test_token_verify_wrong_key() -> None:
    raw_token, _ = issue_token("gid", "rid", "GATE:step", "mcp", KEY)
    _, err = verify_token(raw_token, b"wrong-key-32-bytes-padded-00000")
    assert err == "tampered"
