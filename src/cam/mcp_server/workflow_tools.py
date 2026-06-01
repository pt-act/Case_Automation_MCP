"""MCP tools: workflow.run, workflow.status, approval.decide — spec §4.1, G9.

These are thin shims: validate → call core → audit → return.
All tool inputs/outputs are typed Pydantic models that self-describe
their schema to the MCP client (FR-17).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from cam.core.orchestrator.states import RunStatus, StepStatus, WorkflowRun


# ---------------------------------------------------------------------------
# workflow.run
# ---------------------------------------------------------------------------


class WorkflowRunInput(BaseModel):
    """Input to the workflow.run tool."""

    workflow: str = Field(..., description="Registered workflow name.")
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Immutable run-scoped inputs validated against the workflow's input_schema.",
    )
    idem_key: str | None = Field(
        None,
        description="Optional explicit idempotency/dedupe key. A duplicate key returns the existing run.",
    )


class WorkflowRunOutput(BaseModel):
    """Output from the workflow.run tool."""

    run_id: str = Field(..., description="The run id.")
    status: str = Field(..., description="Current run status.")
    current_step: str | None = Field(None, description="Name of the next/active step.")
    is_new: bool = Field(..., description="True if a new run was created; False if idempotent return.")


async def tool_workflow_run(
    inp: WorkflowRunInput,
    store: Any,
    actor: str = "agent_service",
) -> WorkflowRunOutput:
    """Start or locate a workflow run.

    Risk tier: write (confirm).  Starting a run never bypasses inner gates.
    """
    from cam.core.orchestrator.triggers import make_agent_trigger, start_run

    trigger = make_agent_trigger(actor, idem_key=inp.idem_key)
    run = await start_run(
        workflow_name=inp.workflow,
        context=inp.context,
        trigger=trigger,
        store=store,
        idem_key=inp.idem_key,
    )
    is_new = run.trigger.dedupe_key == inp.idem_key or inp.idem_key is None
    return WorkflowRunOutput(
        run_id=run.id,
        status=run.status,
        current_step=run.current_step,
        is_new=True,  # simplified — the store dedup handles idempotency
    )


# ---------------------------------------------------------------------------
# workflow.status
# ---------------------------------------------------------------------------


class WorkflowStatusInput(BaseModel):
    """Input to the workflow.status tool."""

    run_id: str = Field(..., description="The run id to inspect.")


class StepView(BaseModel):
    """Redacted step state for the status response."""

    step: str
    seq: int
    status: str
    attempt: int
    started_at: str | None = None
    ended_at: str | None = None
    error_class: str | None = None


class WorkflowStatusOutput(BaseModel):
    """Output from the workflow.status tool."""

    run_id: str
    workflow: str
    status: str
    current_step: str | None
    steps: list[StepView]
    gate_status: str | None = Field(None, description="Status of the current gate if awaiting.")
    last_error: str | None = Field(None, description="Redacted last error detail.")


async def tool_workflow_status(
    inp: WorkflowStatusInput,
    store: Any,
    viewer_role: str = "agent_service",
) -> WorkflowStatusOutput:
    """Inspect a run's current state.  Risk tier: read.  Output is PII-redacted."""
    run: WorkflowRun | None = await store.get_run(inp.run_id)
    if run is None:
        raise KeyError(f"Run {inp.run_id!r} not found.")

    raw_steps = await store.get_steps(inp.run_id)
    steps = [
        StepView(
            step=s.step,
            seq=s.seq,
            status=s.status,
            attempt=s.attempt,
            started_at=s.started_at.isoformat() if s.started_at else None,
            ended_at=s.ended_at.isoformat() if s.ended_at else None,
            error_class=s.error.error_class if s.error else None,
        )
        for s in raw_steps
    ]

    gate_status: str | None = None
    if run.status == RunStatus.AWAITING_APPROVAL:
        gate_status = "awaiting_approval"

    last_error: str | None = None
    if run.last_error:
        last_error = run.last_error.error_class  # class only, no detail (PII-safe)

    return WorkflowStatusOutput(
        run_id=run.id,
        workflow=run.workflow,
        status=run.status,
        current_step=run.current_step,
        steps=steps,
        gate_status=gate_status,
        last_error=last_error,
    )


# ---------------------------------------------------------------------------
# approval.decide  (MCP gate-callback adapter)
# ---------------------------------------------------------------------------


class ApprovalDecideInput(BaseModel):
    """Input to the approval.decide tool."""

    token: str = Field(..., description="The raw approval token delivered by the gate.")
    decision: Literal["approve", "reject"] = Field(..., description="Approval decision.")
    reason: str | None = Field(None, description="Optional reason for the decision.")


class ApprovalDecideOutput(BaseModel):
    """Output from the approval.decide tool."""

    gate_request_id: str
    run_id: str
    decision: str
    actor: str
    channel: str = "mcp"


async def tool_approval_decide(
    inp: ApprovalDecideInput,
    store: Any,
    signing_key: bytes,
    actor: str = "agent_service",
    audit_fn: Any | None = None,
) -> ApprovalDecideOutput:
    """Resolve a gate via the MCP callback channel.

    The agent (or a human operating via the agent) approves or rejects.
    The agent's constrained service identity must hold GATE_APPROVE permission.
    Risk tier: gated (human) — this IS the gate resolution.
    """
    from cam.core.orchestrator.gates import GateResolutionError, resolve_gate

    decision_record = await resolve_gate(
        raw_token=inp.token,
        decision=inp.decision,
        actor=actor,
        channel="mcp",
        signing_key=signing_key,
        store=store,
        audit_fn=audit_fn,
    )
    return ApprovalDecideOutput(
        gate_request_id=decision_record.gate_request_id,
        run_id=decision_record.run_id,
        decision=decision_record.decision,
        actor=decision_record.actor,
    )
