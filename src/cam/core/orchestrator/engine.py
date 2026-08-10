"""Workflow run engine — step executor, retry/compensation, parking — spec §5, G3, G5.

The engine drives a single run to completion or a stop state (gate/park/done).
It is called by the worker (Celery task or in-process for tests).

Key invariants:
  - Steps run in declaration order.
  - Each step's effect is idempotency-gated before execution.
  - Transient errors retry with exponential-jitter backoff up to max_attempts.
  - Fatal errors and exhausted retries park the run (never silent-fail).
  - Compensations run in reverse order of registration on park.
  - A GATE:* step parks the run in awaiting_approval and yields.
"""

from __future__ import annotations

import asyncio
import secrets as _secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast

import structlog

from cam.connectors.errors import ConnectorError
from cam.core.orchestrator.dsl import GateConfig, StepContext, get_workflow_latest
from cam.core.orchestrator.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from cam.core.orchestrator.states import (
    GateRequest,
    RunStatus,
    StepError,
    StepState,
    StepStatus,
    WorkflowRun,
)

log = structlog.get_logger(__name__)

MAX_ATTEMPTS = 5
BASE_DELAY = 0.5
MAX_DELAY = 30.0


def _jitter(attempt: int, base: float = BASE_DELAY, cap: float = MAX_DELAY) -> float:
    return _secrets.SystemRandom().uniform(0, min(cap, base * (2 ** attempt)))


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class WorkflowEngine:
    """Drives a single workflow run to completion or a stop state.

    Inject store (RunStore) and idem_store (IdempotencyStore) — both default
    to in-memory implementations for tests.
    """

    def __init__(
        self,
        store: Any,
        idem_store: IdempotencyStore | None = None,
        audit_fn: Callable | None = None,
        signing_key: bytes = b"test-key-32-bytes-padded-0000000",
    ) -> None:
        self.store = store
        self.idem = idem_store or InMemoryIdempotencyStore()
        self.audit = audit_fn
        self.signing_key = signing_key

    async def execute(self, run_id: str) -> WorkflowRun:
        """Advance the run until it reaches a terminal/gate/park state."""
        run = await self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"Run {run_id!r} not found.")

        # Transition queued → running
        if run.status == RunStatus.QUEUED:
            await self.store.update_run_status(run_id, RunStatus.RUNNING)
            run = await self.store.get_run(run_id)

        defn = get_workflow_latest(run.workflow)
        steps = await self.store.get_steps(run_id)

        # Track accumulated step outputs (for ctx.output())
        outputs: dict[str, Any] = {
            s.step: s.output for s in steps if s.output is not None
        }
        # Track registered compensations for this execution pass
        compensations: list[Callable] = []

        for step_state in steps:
            if step_state.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED,
                StepStatus.COMPENSATED):
                # Populate outputs for downstream steps even on skip
                if step_state.output is not None:
                    outputs[step_state.step] = step_state.output
                continue

            step_name = step_state.step

            # ── Gate step ──────────────────────────────────────────────
            if defn.is_gate(step_name):
                await self._handle_gate(run, step_state, defn.gate_config(step_name))
                result = await self.store.get_run(run_id)
                assert result is not None
                return result

            # ── Normal step ────────────────────────────────────────────
            idem_key = step_state.idem_key
            existing = await self.idem.lookup(idem_key)
            if existing is not None:
                # Short-circuit — re-use stored output (resume invariant)
                outputs[step_name] = existing
                log.debug("engine.step_shortcircuit", run_id=run_id, step=step_name)
                await self.store.set_current_step(run_id, step_name)
                continue

            # Mark step running
            now = datetime.now(tz=UTC)
            step_state = step_state.model_copy(update={"status": StepStatus.RUNNING
                , "started_at": now})
            await self.store.update_step(step_state)

            ctx = StepContext(
                run_id=run_id,
                context=run.context,
                step_outputs=dict(outputs),
                idem_key=idem_key,
            )

            try:
                # Start an OTel span for this step, correlated by run_id
                from opentelemetry.trace import use_span

                from cam.obs.observability import start_workflow_span
                _span = start_workflow_span(
                    run.workflow, run_id=run_id, step=step_name
                )
                with use_span(_span, end_on_exit=True):
                    handler = defn.handlers[step_name]
                    output = await handler(ctx)
                if output is None:
                    output = {}

                # Reserve + record idempotency
                await self.idem.reserve(idem_key)
                await self.idem.record(idem_key, output)
                outputs[step_name] = output
                compensations.extend(ctx.compensations)

                now = datetime.now(tz=UTC)
                step_state = step_state.model_copy(
                    update={
                        "status": StepStatus.SUCCEEDED,
                        "output": output,
                        "ended_at": now,
                        "attempt": step_state.attempt + 1,
                    }
                )
                await self.store.update_step(step_state)
                await self._audit("step.succeeded", run_id, {"step": step_name}, output)
                await self.store.set_current_step(run_id, step_name)

            except Exception as exc:
                err = _classify_error(exc, step_state.attempt)
                step_state = step_state.model_copy(
                    update={
                        "status": StepStatus.FAILED,
                        "error": err,
                        "attempt": step_state.attempt + 1,
                        "ended_at": datetime.now(tz=UTC),
                    }
                )
                await self.store.update_step(step_state)

                if err.retryable and step_state.attempt < MAX_ATTEMPTS:
                    # Schedule retry with jitter (caller re-enqueues)
                    delay = _jitter(step_state.attempt)
                    log.warning(
                        "engine.step_retry",
                        run_id=run_id,
                        step=step_name,
                        attempt=step_state.attempt,
                        delay=round(delay, 2),
                    )
                    await asyncio.sleep(delay)
                    # Re-execute this step (reset status to pending)
                    step_state = step_state.model_copy(update={"status": StepStatus.PENDING})
                    await self.store.update_step(step_state)
                    # Re-run the whole execute() so we re-enter the loop correctly
                    return await self.execute(run_id)
                else:
                    await self._park(run_id, err, compensations)
                    parked = await self.store.get_run(run_id)
                    assert parked is not None
                    return parked

        # All steps completed
        await self.store.update_run_status(run_id, RunStatus.SUCCEEDED)
        await self._audit("run.succeeded", run_id, {}, {})
        log.info("engine.run_succeeded", run_id=run_id)
        final = await self.store.get_run(run_id)
        assert final is not None
        return final

    async def _handle_gate(
        self,
        run: WorkflowRun,
        step_state: StepState,
        gate_cfg: GateConfig,
    ) -> None:
        from datetime import timedelta

        from cam.core.orchestrator.gates import issue_token

        gate_id = str(uuid.uuid4())
        now = datetime.now(tz=UTC)
        _approval_channel = Literal["mcp", "web", "email"]
        typed_channels = cast(list[_approval_channel], gate_cfg.channels)
        gate = GateRequest(
            id=gate_id,
            run_id=run.id,
            step=step_state.step,
            required_role=gate_cfg.required_role,
            quorum=gate_cfg.quorum,
            channels=typed_channels,
            created_at=now,
            expires_at=now + timedelta(seconds=gate_cfg.ttl_seconds),
        )
        await self.store.create_gate_request(gate)

        # Issue a token per configured channel
        for channel in typed_channels:
            raw_token, token_record = issue_token(
                gate_request_id=gate_id,
                run_id=run.id,
                step=step_state.step,
                channel=channel,
                signing_key=self.signing_key,
                ttl_seconds=gate_cfg.ttl_seconds,
            )
            await self.store.save_token(token_record)
            log.info(
                "engine.gate_token_issued",
                run_id=run.id,
                step=step_state.step,
                channel=channel,
                token_id=token_record.id,
            )

        await self.store.update_run_status(run.id, RunStatus.AWAITING_APPROVAL)
        await self._audit(
            "gate.requested",
            run.id,
            {"step": step_state.step, "gate_id": gate_id, "channels": gate_cfg.channels},
            {},
        )
        log.info("engine.gate_parked", run_id=run.id, step=step_state.step)

    async def _park(
        self,
        run_id: str,
        err: StepError,
        compensations: list[Callable],
    ) -> None:
        # Run compensations in reverse order (best-effort, each idempotent)
        for fn in reversed(compensations):
            try:
                await fn() if asyncio.iscoroutinefunction(fn) else fn()
            except Exception as comp_exc:
                log.warning("engine.compensation_failed", run_id=run_id, error=str(comp_exc))

        await self.store.update_run_status(run_id, RunStatus.PARKED, last_error=err)
        await self._audit("run.parked", run_id, {"error": err.model_dump()}, {})
        log.warning("engine.run_parked", run_id=run_id, error=err.detail)

    async def _audit(self, action: str, run_id: str, inputs: dict, outputs: dict) -> None:
        if self.audit is None:
            return
        try:
            await self.audit(
                actor="workflow_engine",
                action=action,
                inputs=inputs,
                outputs=outputs,
                run_id=run_id,
            )
        except Exception:
            pass


def _classify_error(exc: Exception, attempt: int) -> StepError:
    if isinstance(exc, ConnectorError):
        return StepError(
            error_class=type(exc).__name__,
            detail=exc.detail,
            attempt=attempt,
            retryable=exc.retryable,
        )
    return StepError(
        error_class=type(exc).__name__,
        detail=str(exc)[:200],
        attempt=attempt,
        retryable=False,
    )
