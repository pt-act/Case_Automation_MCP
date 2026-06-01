"""deadline.schedule MCP tool + in-memory store + Scheduler abstraction — spec §4.1, G4.

Risk tier: write (confirm). Idempotent on idempotency_key.
Past-due-on-create → flagged, non-armed, never silently persisted as armed.
Each reminder is armed via TWO independent scheduler paths sharing one idem key.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable

import structlog

from cam.core.services.deadline.calendar import get_calendar
from cam.core.services.deadline.compute import compute_due_date, preview_reminders
from cam.core.services.deadline.types import (
    DeadlineRule,
    ReminderOffset,
    RuleRef,
    ScheduledDeadline,
    ScheduledReminder,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Scheduler port
# ---------------------------------------------------------------------------


@runtime_checkable
class Scheduler(Protocol):
    """Abstract scheduler — Celery beat in production, in-memory for tests."""

    async def arm(self, fire_at: datetime, job_ref: str, idem_key: str) -> str:
        """Schedule a job; return an arm_id."""
        ...

    async def cancel(self, arm_id: str) -> None: ...

    async def heartbeat(self) -> datetime:
        """Return the last heartbeat timestamp."""
        ...


class InMemoryScheduler:
    """Deterministic in-memory scheduler for tests."""

    def __init__(self) -> None:
        self._arms: dict[str, dict] = {}
        self._last_heartbeat: datetime = datetime.now(tz=timezone.utc)
        self.stopped: bool = False

    async def arm(self, fire_at: datetime, job_ref: str, idem_key: str) -> str:
        arm_id = str(uuid.uuid4())
        self._arms[arm_id] = {"fire_at": fire_at, "job_ref": job_ref, "idem_key": idem_key}
        return arm_id

    async def cancel(self, arm_id: str) -> None:
        self._arms.pop(arm_id, None)

    async def heartbeat(self) -> datetime:
        if not self.stopped:
            self._last_heartbeat = datetime.now(tz=timezone.utc)
        return self._last_heartbeat

    def stop(self) -> None:
        self.stopped = True


# ---------------------------------------------------------------------------
# In-memory deadline store (tests; production uses SQLAlchemy)
# ---------------------------------------------------------------------------


class DeadlineStore:
    """In-memory store for ScheduledDeadline and ScheduledReminder."""

    def __init__(self) -> None:
        self._deadlines: dict[str, ScheduledDeadline] = {}
        self._by_idem: dict[str, str] = {}  # idem_key → deadline_id

    def save(self, sd: ScheduledDeadline) -> ScheduledDeadline:
        self._deadlines[sd.deadline_id] = sd
        if sd.idempotency_key:
            self._by_idem[sd.idempotency_key] = sd.deadline_id
        return sd

    def get(self, deadline_id: str) -> ScheduledDeadline | None:
        return self._deadlines.get(deadline_id)

    def get_by_idem(self, idem_key: str) -> ScheduledDeadline | None:
        did = self._by_idem.get(idem_key)
        return self._deadlines.get(did) if did else None

    def list_for_matter(self, matter_id: str) -> list[ScheduledDeadline]:
        return [d for d in self._deadlines.values() if d.matter_id == matter_id]

    def upcoming_reminders(
        self, window_days: int = 90, matter_id: str | None = None
    ) -> list[ScheduledReminder]:
        cutoff = datetime.now(tz=timezone.utc) + timedelta(days=window_days)
        out: list[ScheduledReminder] = []
        for sd in self._deadlines.values():
            if matter_id and sd.matter_id != matter_id:
                continue
            for rem in sd.reminders:
                if rem.status == "armed" and rem.fire_at <= cutoff:
                    out.append(rem)
        return sorted(out, key=lambda r: r.fire_at)


# ---------------------------------------------------------------------------
# deadline.schedule tool
# ---------------------------------------------------------------------------


class PastDueOnCreateError(Exception):
    """Raised when a deadline would be past-due at create time."""

    def __init__(self, due_at: datetime, now: datetime) -> None:
        self.due_at = due_at
        self.now = now
        super().__init__(f"Deadline due_at={due_at.isoformat()} is in the past (now={now.isoformat()}).")


async def tool_deadline_schedule(
    *,
    matter_id: str,
    rule: DeadlineRule,
    trigger_inputs: dict[str, datetime],
    rule_ref: RuleRef,
    name: str,
    idempotency_key: str,
    store: DeadlineStore,
    scheduler: Scheduler,
    audit_fn: Any | None = None,
    now: datetime | None = None,
) -> ScheduledDeadline:
    """deadline.schedule tool — risk tier write (confirm). Idempotent on idempotency_key.

    Returns the ScheduledDeadline (existing if already scheduled, new if not).
    Raises PastDueOnCreateError if due_at < now (caller must surface to human).
    """
    now = now or datetime.now(tz=timezone.utc)

    # Idempotency check
    existing = store.get_by_idem(idempotency_key)
    if existing is not None:
        log.debug("deadline.schedule.idempotent", idem_key=idempotency_key)
        return existing

    # Compute
    trace = compute_due_date(rule, trigger_inputs, as_of=now, rule_ref=rule_ref)

    # Past-due-on-create guard
    if trace.due_at < now:
        log.warning(
            "deadline.past_due_on_create",
            rule_id=rule.rule_id,
            due_at=trace.due_at.isoformat(),
            matter_id=matter_id,
        )
        flagged = ScheduledDeadline(
            deadline_id=str(uuid.uuid4()),
            matter_id=matter_id,
            rule_ref=rule_ref,
            trace=trace,
            reminders=[],
            flagged_past_due_on_create=True,
            idempotency_key=idempotency_key,
        )
        store.save(flagged)
        await _audit(audit_fn, "deadline.past_due_on_create", matter_id, trace, flagged.deadline_id)
        raise PastDueOnCreateError(trace.due_at, now)

    # Resolve reminders
    cal = get_calendar(rule.calendar_id)
    reminders: list[ScheduledReminder] = []
    for rem_offset in rule.reminders:
        if rem_offset.unit == "business_days":
            fire_date = cal.add_business_days(trace.due_at.date(), rem_offset.amount)
        else:
            fire_date = (trace.due_at + timedelta(days=rem_offset.amount)).date()
        fire_at = datetime.combine(fire_date, trace.due_at.time(), tzinfo=trace.due_at.tzinfo)

        idem = f"{idempotency_key}:{rem_offset.amount}:{rem_offset.unit}"
        if fire_at >= now:
            # Arm via TWO independent scheduler paths (redundancy)
            arm1 = await scheduler.arm(fire_at, f"deadline_reminder:{idempotency_key}", idem)
            arm2 = await scheduler.arm(fire_at, f"deadline_reminder_backup:{idempotency_key}", idem)
            reminders.append(ScheduledReminder(
                id=str(uuid.uuid4()),
                deadline_id="",  # filled after persist
                fire_at=fire_at,
                offset=rem_offset,
                status="armed",
                idempotency_key=idem,
                redundant_arm_ids=[arm1, arm2],
            ))
        else:
            reminders.append(ScheduledReminder(
                id=str(uuid.uuid4()),
                deadline_id="",
                fire_at=fire_at,
                offset=rem_offset,
                status="skipped_past",
                idempotency_key=idem,
            ))
            log.info("deadline.reminder_skipped_past", fire_at=fire_at.isoformat(), idem=idem)

    deadline_id = str(uuid.uuid4())
    # Backfill deadline_id into reminders
    reminders = [r.model_copy(update={"deadline_id": deadline_id}) for r in reminders]

    sd = ScheduledDeadline(
        deadline_id=deadline_id,
        matter_id=matter_id,
        rule_ref=rule_ref,
        trace=trace,
        reminders=sorted(reminders, key=lambda r: r.fire_at),
        flagged_past_due_on_create=False,
        idempotency_key=idempotency_key,
    )
    store.save(sd)
    await _audit(audit_fn, "deadline.scheduled", matter_id, trace, deadline_id)
    log.info("deadline.scheduled", deadline_id=deadline_id, due_at=trace.due_at.isoformat())
    return sd


async def _audit(audit_fn: Any, action: str, matter_id: str, trace: Any, deadline_id: str) -> None:
    if audit_fn is None:
        return
    try:
        await audit_fn(
            actor="deadline_engine",
            action=action,
            inputs={"matter_id": matter_id, "rule_id": trace.rule_ref.rule_id},
            outputs={"deadline_id": deadline_id, "due_at": trace.due_at.isoformat()},
        )
    except Exception:
        pass
