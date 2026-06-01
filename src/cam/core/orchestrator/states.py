"""Run/step state machines and orchestrator Pydantic domain types — spec §3, §5.2, §8."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Status enums
# ---------------------------------------------------------------------------


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    PARKED = "parked"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATED = "compensated"
    SKIPPED = "skipped"


# Legal state transitions — only edges listed here are allowed (spec §5.2).
_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset([RunStatus.RUNNING]),
    RunStatus.RUNNING: frozenset([
        RunStatus.SUCCEEDED,
        RunStatus.AWAITING_APPROVAL,
        RunStatus.PARKED,
        RunStatus.CANCELLED,
    ]),
    RunStatus.AWAITING_APPROVAL: frozenset([
        RunStatus.RUNNING,   # approved
        RunStatus.REJECTED,  # rejected
        RunStatus.CANCELLED,
    ]),
    RunStatus.PARKED: frozenset([
        RunStatus.RUNNING,   # re-drive
        RunStatus.CANCELLED,
    ]),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.REJECTED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

_STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset([StepStatus.RUNNING]),
    StepStatus.RUNNING: frozenset([StepStatus.SUCCEEDED, StepStatus.FAILED]),
    StepStatus.FAILED: frozenset([StepStatus.RUNNING, StepStatus.COMPENSATED]),  # retry or compensate
    StepStatus.SUCCEEDED: frozenset([StepStatus.COMPENSATED]),
    StepStatus.COMPENSATED: frozenset(),
    StepStatus.SKIPPED: frozenset(),
}


def is_legal_run_transition(from_status: RunStatus, to_status: RunStatus) -> bool:
    return to_status in _RUN_TRANSITIONS.get(from_status, frozenset())


def is_legal_step_transition(from_status: StepStatus, to_status: StepStatus) -> bool:
    return to_status in _STEP_TRANSITIONS.get(from_status, frozenset())


# ---------------------------------------------------------------------------
# Orchestrator domain types (Pydantic v2)
# ---------------------------------------------------------------------------


class StepError(BaseModel):
    """Captured error from a failed step."""

    error_class: str
    detail: str
    attempt: int
    retryable: bool


class TriggerRef(BaseModel):
    """How a run was started."""

    kind: Literal["event", "schedule", "agent"] = Field(
        ..., description="Trigger source kind."
    )
    source: str = Field(..., description="Event id / schedule id / agent identity.")
    dedupe_key: str | None = Field(
        None, description="Stable key for idempotent start (event id or explicit key)."
    )


class WorkflowRun(BaseModel):
    """A single workflow execution — the top-level durable unit."""

    id: str = Field(..., description="Run id (uuid).")
    workflow: str = Field(..., description="Registered workflow name.")
    workflow_version: int = Field(..., description="Pinned definition version.")
    status: RunStatus = Field(RunStatus.QUEUED, description="Current run status.")
    current_step: str | None = Field(None, description="Name of the next/active step.")
    trigger: TriggerRef = Field(..., description="How the run was triggered.")
    context: dict[str, Any] = Field(
        default_factory=dict, description="Immutable run-scoped inputs."
    )
    created_at: datetime = Field(..., description="UTC creation timestamp.")
    updated_at: datetime = Field(..., description="UTC last-updated timestamp.")
    last_error: StepError | None = Field(None, description="Last step error if parked.")


class StepState(BaseModel):
    """Persisted state for a single step within a run."""

    run_id: str
    step: str = Field(..., description="Step name.")
    seq: int = Field(..., description="Ordinal position in the workflow.")
    status: StepStatus = Field(StepStatus.PENDING)
    attempt: int = Field(0, description="Retry counter.")
    idem_key: str = Field(..., description="f'{run_id}:{step}'")
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: StepError | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class GateRequest(BaseModel):
    """A human-approval gate blocking a run at a GATE:* step."""

    id: str
    run_id: str
    step: str
    risk_tier: Literal["gated (human)"] = "gated (human)"
    required_role: str = Field("attorney", description="RBAC role permitted to approve.")
    quorum: int = Field(1, description="N-of-M approvals required.")
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"
    channels: list[Literal["mcp", "web", "email"]] = Field(
        default_factory=lambda: ["mcp", "web", "email"]
    )
    created_at: datetime
    expires_at: datetime


class ApprovalDecision(BaseModel):
    """A recorded approval or rejection for a gate."""

    gate_request_id: str
    run_id: str
    step: str
    decision: Literal["approve", "reject"]
    actor: str
    channel: Literal["mcp", "web", "email"]
    token_id: str
    reason: str | None = None
    decided_at: datetime


class ApprovalToken(BaseModel):
    """A single-use approval token (only the hash is persisted)."""

    id: str = Field(..., description="Token id (also embedded in the raw token).")
    gate_request_id: str
    run_id: str
    step: str
    key_id: str = Field(..., description="Signing key id from secret store.")
    token_hash: str = Field(..., description="SHA-256 hex digest of the raw token.")
    channel: Literal["mcp", "web", "email"]
    expires_at: datetime
    used_at: datetime | None = None

    @property
    def is_expired(self) -> bool:
        from datetime import timezone
        return datetime.now(tz=timezone.utc) > self.expires_at

    @property
    def is_used(self) -> bool:
        return self.used_at is not None
