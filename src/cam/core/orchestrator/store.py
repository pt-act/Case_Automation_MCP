"""In-memory RunStore — used by the engine for unit tests and local dev.

The production implementation is the SQLAlchemy repository in
`cam/persistence/` (wired in `cam/core/orchestrator/repository.py`).
Both share the same async interface so the engine works against either.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cam.core.orchestrator.states import (
    ApprovalDecision,
    ApprovalToken,
    GateRequest,
    RunStatus,
    StepState,
    StepStatus,
    WorkflowRun,
    is_legal_run_transition,
    is_legal_step_transition,
)


class IllegalTransitionError(Exception):
    pass


class InMemoryRunStore:
    """Deterministic in-memory store — deterministic ordering, no DB needed."""

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}
        self._steps: dict[str, list[StepState]] = {}   # run_id → ordered list
        self._gates: dict[str, GateRequest] = {}        # gate_request_id
        self._tokens: dict[str, ApprovalToken] = {}     # token_id
        self._decisions: list[ApprovalDecision] = []
        self._dedupe: dict[str, str] = {}               # dedupe_key → run_id

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def create_run(self, run: WorkflowRun, steps: list[StepState]) -> WorkflowRun:
        if run.trigger.dedupe_key and run.trigger.dedupe_key in self._dedupe:
            existing_id = self._dedupe[run.trigger.dedupe_key]
            return self._runs[existing_id]
        self._runs[run.id] = run
        self._steps[run.id] = list(steps)
        if run.trigger.dedupe_key:
            self._dedupe[run.trigger.dedupe_key] = run.id
        return run

    async def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    async def update_run_status(
        self,
        run_id: str,
        status: RunStatus,
        current_step: str | None = None,
        last_error: Any | None = None,
    ) -> None:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"Run {run_id!r} not found.")
        if not is_legal_run_transition(run.status, status):
            raise IllegalTransitionError(
                f"Run {run_id}: {run.status!r} → {status!r} is not a legal transition."
            )
        updates: dict[str, Any] = {
            "status": status,
            "updated_at": datetime.now(tz=UTC),
        }
        if current_step is not None:
            updates["current_step"] = current_step
        if last_error is not None:
            updates["last_error"] = last_error
        self._runs[run_id] = run.model_copy(update=updates)

    async def set_current_step(self, run_id: str, step: str) -> None:
        """Update current_step without a status transition (run stays RUNNING)."""
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"Run {run_id!r} not found.")
        self._runs[run_id] = run.model_copy(
            update={"current_step": step, "updated_at": datetime.now(tz=UTC)}
        )

    async def advance_gate_step(self, run_id: str, step_name: str) -> None:
        """Mark a gate step SUCCEEDED after approval.

        Gate steps never execute a handler, so they go directly PENDING → SUCCEEDED
        when the gate is resolved.  This bypasses the normal transition guard (which
        enforces PENDING → RUNNING → SUCCEEDED for application steps).
        """
        steps = self._steps.get(run_id, [])
        for i, s in enumerate(steps):
            if s.step == step_name:
                steps[i] = s.model_copy(
                    update={
                        "status": StepStatus.SUCCEEDED,
                        "ended_at": datetime.now(tz=UTC),
                    }
                )
                return

    async def find_queued_runs(self) -> list[str]:
        return [r.id for r in self._runs.values() if r.status == RunStatus.QUEUED]

    async def find_recoverable_runs(self) -> list[str]:
        """Runs in queued/running state for crash-recovery sweep."""
        return [
            r.id for r in self._runs.values()
            if r.status in (RunStatus.QUEUED, RunStatus.RUNNING)
        ]

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    async def get_steps(self, run_id: str) -> list[StepState]:
        return list(self._steps.get(run_id, []))

    async def get_step(self, run_id: str, step_name: str) -> StepState | None:
        for s in self._steps.get(run_id, []):
            if s.step == step_name:
                return s
        return None

    async def update_step(self, step: StepState) -> None:
        steps = self._steps.get(step.run_id, [])
        for i, s in enumerate(steps):
            if s.step == step.step:
                if not is_legal_step_transition(s.status, step.status):
                    raise IllegalTransitionError(
                        f"Step {step.step!r}: {s.status!r} → {step.status!r} is illegal."
                    )
                steps[i] = step
                return
        raise KeyError(f"Step {step.step!r} not found in run {step.run_id!r}.")

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    async def create_gate_request(self, gate: GateRequest) -> GateRequest:
        self._gates[gate.id] = gate
        return gate

    async def get_gate_request(self, gate_id: str) -> GateRequest | None:
        return self._gates.get(gate_id)

    async def update_gate_request(self, gate: GateRequest) -> None:
        self._gates[gate.id] = gate

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------

    async def save_token(self, token: ApprovalToken) -> None:
        self._tokens[token.id] = token

    async def get_token(self, token_id: str) -> ApprovalToken | None:
        return self._tokens.get(token_id)

    async def mark_token_used(self, token_id: str, used_at: datetime) -> bool:
        token = self._tokens.get(token_id)
        if token is None or token.is_used:
            return False
        self._tokens[token_id] = token.model_copy(update={"used_at": used_at})
        return True

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    async def save_approval_decision(self, decision: ApprovalDecision) -> None:
        self._decisions.append(decision)

    def all_decisions(self) -> list[ApprovalDecision]:
        return list(self._decisions)
