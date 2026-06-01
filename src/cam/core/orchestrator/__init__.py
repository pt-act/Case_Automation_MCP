"""Workflow orchestration engine — durable state-machine, gates, idempotency, recovery."""

from cam.core.orchestrator.channels import (
    ApprovalChannel,
    EmailApprovalChannel,
    MCPApprovalChannel,
    WebApprovalChannel,
)
from cam.core.orchestrator.dsl import GateConfig, StepContext, WorkflowDef, workflow
from cam.core.orchestrator.engine import WorkflowEngine
from cam.core.orchestrator.gates import GateResolutionError, issue_token, resolve_gate, verify_token
from cam.core.orchestrator.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from cam.core.orchestrator.recovery import recovery_sweep
from cam.core.orchestrator.states import (
    ApprovalDecision,
    ApprovalToken,
    GateRequest,
    RunStatus,
    StepError,
    StepState,
    StepStatus,
    TriggerRef,
    WorkflowRun,
    is_legal_run_transition,
    is_legal_step_transition,
)
from cam.core.orchestrator.store import IllegalTransition, InMemoryRunStore
from cam.core.orchestrator.triggers import make_agent_trigger, make_event_trigger, start_run

__all__ = [
    "ApprovalChannel",
    "ApprovalDecision",
    "ApprovalToken",
    "EmailApprovalChannel",
    "GateConfig",
    "GateRequest",
    "GateResolutionError",
    "IdempotencyStore",
    "IllegalTransition",
    "InMemoryIdempotencyStore",
    "InMemoryRunStore",
    "MCPApprovalChannel",
    "RunStatus",
    "StepContext",
    "StepError",
    "StepState",
    "StepStatus",
    "TriggerRef",
    "WebApprovalChannel",
    "WorkflowDef",
    "WorkflowEngine",
    "WorkflowRun",
    "is_legal_run_transition",
    "is_legal_step_transition",
    "issue_token",
    "make_agent_trigger",
    "make_event_trigger",
    "recovery_sweep",
    "resolve_gate",
    "start_run",
    "verify_token",
    "workflow",
]
