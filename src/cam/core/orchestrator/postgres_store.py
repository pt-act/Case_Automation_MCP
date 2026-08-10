"""PostgreSQL-backed RunStore — production implementation.

Satisfies the same async interface as `InMemoryRunStore` but persists to
PostgreSQL via SQLAlchemy 2 async sessions.  The ORM models
(`WorkflowRunORM`, `WorkflowStepStateORM`, `GateRequestORM`,
`ApprovalDecisionORM`, `ApprovalTokenORM`) and the Alembic migration (002)
already exist — this module is the mapping layer.

Usage::

    from sqlalchemy.ext.asyncio import async_sessionmaker
    from cam.core.orchestrator.postgres_store import PostgresRunStore

    store = PostgresRunStore(session_factory)
    engine = WorkflowEngine(store=store, ...)

Each method opens a short-lived session (per-call connect).  This keeps the
store self-contained — the engine never manages sessions — and is robust to
Celery worker boundaries where a long-held session would be invalid.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cam.core.orchestrator.orm import (
    ApprovalDecisionORM,
    ApprovalTokenORM,
    GateRequestORM,
    WorkflowRunORM,
    WorkflowStepStateORM,
)
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
from cam.core.orchestrator.store import IllegalTransitionError


class PostgresRunStore:
    """Production RunStore backed by PostgreSQL + SQLAlchemy 2 async."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    # ------------------------------------------------------------------
    # Mapping helpers — Pydantic ↔ ORM
    # ------------------------------------------------------------------

    @staticmethod
    def _run_to_orm(run: WorkflowRun) -> WorkflowRunORM:
        return WorkflowRunORM(
            id=run.id,
            workflow=run.workflow,
            workflow_version=run.workflow_version,
            status=run.status.value,
            current_step=run.current_step,
            trigger=run.trigger.model_dump(mode="json"),
            context=run.context,
            last_error=run.last_error.model_dump(mode="json") if run.last_error else None,
            dedupe_key=run.trigger.dedupe_key,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    @staticmethod
    def _run_from_orm(orm: WorkflowRunORM) -> WorkflowRun:
        return WorkflowRun(
            id=orm.id,
            workflow=orm.workflow,
            workflow_version=orm.workflow_version,
            status=RunStatus(orm.status),
            current_step=orm.current_step,
            trigger=TriggerRef.model_validate(orm.trigger),
            context=orm.context or {},
            last_error=StepError.model_validate(orm.last_error) if orm.last_error else None,
            created_at=orm.created_at,
            updated_at=orm.updated_at,
        )

    @staticmethod
    def _step_to_orm(step: StepState) -> WorkflowStepStateORM:
        return WorkflowStepStateORM(
            run_id=step.run_id,
            step=step.step,
            seq=step.seq,
            status=step.status.value,
            attempt=step.attempt,
            idem_key=step.idem_key,
            input=step.input,
            output=step.output,
            error=step.error.model_dump(mode="json") if step.error else None,
            started_at=step.started_at,
            ended_at=step.ended_at,
        )

    @staticmethod
    def _step_from_orm(orm: WorkflowStepStateORM) -> StepState:
        return StepState(
            run_id=orm.run_id,
            step=orm.step,
            seq=orm.seq,
            status=StepStatus(orm.status),
            attempt=orm.attempt,
            idem_key=orm.idem_key,
            input=orm.input,
            output=orm.output,
            error=StepError.model_validate(orm.error) if orm.error else None,
            started_at=orm.started_at,
            ended_at=orm.ended_at,
        )

    @staticmethod
    def _gate_to_orm(gate: GateRequest) -> GateRequestORM:
        return GateRequestORM(
            id=gate.id,
            run_id=gate.run_id,
            step=gate.step,
            required_role=gate.required_role,
            quorum=gate.quorum,
            status=gate.status,
            channels=list(gate.channels),
            created_at=gate.created_at,
            expires_at=gate.expires_at,
        )

    @staticmethod
    def _gate_from_orm(orm: GateRequestORM) -> GateRequest:
        return GateRequest(
            id=orm.id,
            run_id=orm.run_id,
            step=orm.step,
            required_role=orm.required_role,
            quorum=orm.quorum,
            status=orm.status,
            channels=list(orm.channels),
            created_at=orm.created_at,
            expires_at=orm.expires_at,
        )

    @staticmethod
    def _token_to_orm(token: ApprovalToken) -> ApprovalTokenORM:
        return ApprovalTokenORM(
            id=token.id,
            gate_request_id=token.gate_request_id,
            run_id=token.run_id,
            step=token.step,
            key_id=token.key_id,
            token_hash=token.token_hash,
            channel=token.channel,
            expires_at=token.expires_at,
            used_at=token.used_at,
        )

    @staticmethod
    def _token_from_orm(orm: ApprovalTokenORM) -> ApprovalToken:
        return ApprovalToken(
            id=orm.id,
            gate_request_id=orm.gate_request_id,
            run_id=orm.run_id,
            step=orm.step,
            key_id=orm.key_id,
            token_hash=orm.token_hash,
            channel=orm.channel,
            expires_at=orm.expires_at,
            used_at=orm.used_at,
        )

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def create_run(self, run: WorkflowRun, steps: list[StepState]) -> WorkflowRun:
        async with self._factory() as session:
            # Idempotency: check dedupe key first
            if run.trigger.dedupe_key:
                existing = await session.scalar(
                    select(WorkflowRunORM).where(
                        WorkflowRunORM.dedupe_key == run.trigger.dedupe_key
                    )
                )
                if existing is not None:
                    return self._run_from_orm(existing)

            session.add(self._run_to_orm(run))
            for step in steps:
                session.add(self._step_to_orm(step))
            await session.commit()
            return run

    async def get_run(self, run_id: str) -> WorkflowRun | None:
        async with self._factory() as session:
            orm = await session.scalar(
                select(WorkflowRunORM).where(WorkflowRunORM.id == run_id)
            )
            return self._run_from_orm(orm) if orm else None

    async def update_run_status(
        self,
        run_id: str,
        status: RunStatus,
        current_step: str | None = None,
        last_error: Any | None = None,
    ) -> None:
        async with self._factory() as session:
            orm = await session.scalar(
                select(WorkflowRunORM).where(WorkflowRunORM.id == run_id)
            )
            if orm is None:
                raise KeyError(f"Run {run_id!r} not found.")
            current = RunStatus(orm.status)
            if not is_legal_run_transition(current, status):
                raise IllegalTransitionError(
                    f"Run {run_id}: {current!r} → {status!r} is not a legal transition."
                )
            orm.status = status.value
            orm.updated_at = datetime.now(tz=UTC)
            if current_step is not None:
                orm.current_step = current_step
            if last_error is not None:
                orm.last_error = (
                    last_error.model_dump(mode="json")
                    if hasattr(last_error, "model_dump")
                    else last_error
                )
            await session.commit()

    async def set_current_step(self, run_id: str, step: str) -> None:
        async with self._factory() as session:
            orm = await session.scalar(
                select(WorkflowRunORM).where(WorkflowRunORM.id == run_id)
            )
            if orm is None:
                raise KeyError(f"Run {run_id!r} not found.")
            orm.current_step = step
            orm.updated_at = datetime.now(tz=UTC)
            await session.commit()

    async def advance_gate_step(self, run_id: str, step_name: str) -> None:
        async with self._factory() as session:
            orm = await session.scalar(
                select(WorkflowStepStateORM).where(
                    WorkflowStepStateORM.run_id == run_id,
                    WorkflowStepStateORM.step == step_name,
                )
            )
            if orm is None:
                raise KeyError(f"Step {step_name!r} not found in run {run_id!r}.")
            orm.status = StepStatus.SUCCEEDED.value
            orm.ended_at = datetime.now(tz=UTC)
            await session.commit()

    async def find_queued_runs(self) -> list[str]:
        async with self._factory() as session:
            result = await session.scalars(
                select(WorkflowRunORM.id).where(WorkflowRunORM.status == RunStatus.QUEUED.value)
            )
            return list(result)

    async def find_recoverable_runs(self) -> list[str]:
        async with self._factory() as session:
            result = await session.scalars(
                select(WorkflowRunORM.id).where(
                    WorkflowRunORM.status.in_([
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                    ])
                )
            )
            return list(result)

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    async def get_steps(self, run_id: str) -> list[StepState]:
        async with self._factory() as session:
            result = await session.scalars(
                select(WorkflowStepStateORM)
                .where(WorkflowStepStateORM.run_id == run_id)
                .order_by(WorkflowStepStateORM.seq)
            )
            return [self._step_from_orm(orm) for orm in result]

    async def get_step(self, run_id: str, step_name: str) -> StepState | None:
        async with self._factory() as session:
            orm = await session.scalar(
                select(WorkflowStepStateORM).where(
                    WorkflowStepStateORM.run_id == run_id,
                    WorkflowStepStateORM.step == step_name,
                )
            )
            return self._step_from_orm(orm) if orm else None

    async def update_step(self, step: StepState) -> None:
        async with self._factory() as session:
            orm = await session.scalar(
                select(WorkflowStepStateORM).where(
                    WorkflowStepStateORM.run_id == step.run_id,
                    WorkflowStepStateORM.step == step.step,
                )
            )
            if orm is None:
                raise KeyError(f"Step {step.step!r} not found in run {step.run_id!r}.")
            current = StepStatus(orm.status)
            if not is_legal_step_transition(current, step.status):
                raise IllegalTransitionError(
                    f"Step {step.step!r}: {current!r} → {step.status!r} is illegal."
                )
            orm.status = step.status.value
            orm.attempt = step.attempt
            orm.input = step.input
            orm.output = step.output
            orm.error = step.error.model_dump(mode="json") if step.error else None
            orm.started_at = step.started_at
            orm.ended_at = step.ended_at
            await session.commit()

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    async def create_gate_request(self, gate: GateRequest) -> GateRequest:
        async with self._factory() as session:
            session.add(self._gate_to_orm(gate))
            await session.commit()
            return gate

    async def get_gate_request(self, gate_id: str) -> GateRequest | None:
        async with self._factory() as session:
            orm = await session.scalar(
                select(GateRequestORM).where(GateRequestORM.id == gate_id)
            )
            return self._gate_from_orm(orm) if orm else None

    async def update_gate_request(self, gate: GateRequest) -> None:
        async with self._factory() as session:
            orm = await session.scalar(
                select(GateRequestORM).where(GateRequestORM.id == gate.id)
            )
            if orm is None:
                raise KeyError(f"Gate {gate.id!r} not found.")
            orm.status = gate.status
            orm.required_role = gate.required_role
            orm.quorum = gate.quorum
            await session.commit()

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------

    async def save_token(self, token: ApprovalToken) -> None:
        async with self._factory() as session:
            session.add(self._token_to_orm(token))
            await session.commit()

    async def get_token(self, token_id: str) -> ApprovalToken | None:
        async with self._factory() as session:
            orm = await session.scalar(
                select(ApprovalTokenORM).where(ApprovalTokenORM.id == token_id)
            )
            return self._token_from_orm(orm) if orm else None

    async def mark_token_used(self, token_id: str, used_at: datetime) -> bool:
        async with self._factory() as session:
            orm = await session.scalar(
                select(ApprovalTokenORM).where(ApprovalTokenORM.id == token_id)
            )
            if orm is None or orm.used_at is not None:
                return False
            orm.used_at = used_at
            await session.commit()
            return True

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    async def save_approval_decision(self, decision: ApprovalDecision) -> None:
        async with self._factory() as session:
            orm = ApprovalDecisionORM(
                gate_request_id=decision.gate_request_id,
                run_id=decision.run_id,
                step=decision.step,
                decision=decision.decision,
                actor=decision.actor,
                channel=decision.channel,
                token_id=decision.token_id,
                reason=decision.reason,
                decided_at=decision.decided_at,
            )
            session.add(orm)
            await session.commit()
