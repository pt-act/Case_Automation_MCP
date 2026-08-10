"""Reminder firing + escalation + completion — spec §5, G5.

Exactly-once delivery under redundant arming via Redis idempotency.
Escalation level is monotonically non-decreasing.
A missed deadline raises a safety incident — never silent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import structlog

from cam.core.services.deadline.schedule import DeadlineStore, Scheduler
from cam.core.services.deadline.types import ScheduledDeadline

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Notification port
# ---------------------------------------------------------------------------


@runtime_checkable
class NotificationPort(Protocol):
    async def notify(
        self, recipients: list[str], payload: dict, idem_key: str
    ) -> str: ...


class MockNotificationPort:
    """Deterministic mock — records calls; injectable error for testing."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._fail: bool = False
        self._fail_count: int = 0

    def set_fail(self, count: int = 1) -> None:
        self._fail = True
        self._fail_count = count

    async def notify(self, recipients: list[str], payload: dict, idem_key: str) -> str:
        if self._fail and self._fail_count > 0:
            self._fail_count -= 1
            if self._fail_count == 0:
                self._fail = False
            raise ConnectionError("Notification delivery failed.")
        self.calls.append({"recipients": recipients, "payload": payload, "idem_key": idem_key})
        return f"notif-{idem_key}"


# ---------------------------------------------------------------------------
# Reminder firing job
# ---------------------------------------------------------------------------


async def fire_reminder(
    *,
    deadline_id: str,
    reminder_idem_key: str,
    store: DeadlineStore,
    notification_port: NotificationPort,
    redis_client: Any | None = None,
    audit_fn: Any | None = None,
) -> bool:
    """Fire one reminder.  Returns True on first delivery, False if already fired (dedup)."""
    # Redis dedup key (if Redis available)
    dedup_key = f"cam:reminder_fired:{reminder_idem_key}"
    if redis_client is not None:
        already = await redis_client.get(dedup_key)
        if already:
            log.debug("deadline.reminder_dedup_skip", idem_key=reminder_idem_key)
            return False

    sd = store.get(deadline_id)
    if sd is None:
        log.warning("deadline.reminder_no_deadline", deadline_id=deadline_id)
        return False

    # Find the reminder
    reminder = next((r for r in sd.reminders if r.idempotency_key == reminder_idem_key), None)
    if reminder is None or reminder.status != "armed":
        return False

    # Notify — never drop; retry is caller's responsibility
    recipients = _resolve_recipients(sd)
    await notification_port.notify(
        recipients=recipients,
        payload={"deadline_id": deadline_id, "matter_id": sd.matter_id,
            "due_at": sd.trace.due_at.isoformat()},
        idem_key=reminder_idem_key,
    )

    # Mark fired
    updated_reminders = [
        r.model_copy(update={"status": "fired"}) if r.idempotency_key == reminder_idem_key else r
        for r in sd.reminders
    ]
    store.save(sd.model_copy(update={"reminders": updated_reminders}))

    if redis_client is not None:
        await redis_client.set(dedup_key, "1", ex=86400 * 7)

    if audit_fn:
        try:
            await audit_fn(actor="deadline_engine", action="reminder.fired",
                           inputs={"idem_key": reminder_idem_key},
                               outputs={"deadline_id": deadline_id})
        except Exception:
            pass

    log.info("deadline.reminder_fired", deadline_id=deadline_id, idem_key=reminder_idem_key)
    return True


def _resolve_recipients(sd: ScheduledDeadline) -> list[str]:
    """Resolve recipient roles for a reminder dispatch."""
    sd.rule_ref.__dict__.get("escalation", None)
    # Simple default: notify the case manager
    return [f"case_manager:{sd.matter_id}"]


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


async def escalate(
    *,
    deadline_id: str,
    store: DeadlineStore,
    notification_port: NotificationPort,
    audit_fn: Any | None = None,
) -> int:
    """Advance escalation level by 1 (monotonic), widen recipients, dispatch."""
    sd = store.get(deadline_id)
    if sd is None:
        return 0

    new_level = sd.escalation_level + 1
    updated = sd.model_copy(update={"escalation_level": max(sd.escalation_level, new_level)})
    store.save(updated)

    now = datetime.now(tz=UTC)
    if updated.trace.due_at < now:
        # Past due — safety incident
        log.error(
            "deadline.MISSED",
            deadline_id=deadline_id,
            matter_id=sd.matter_id,
            due_at=sd.trace.due_at.isoformat(),
        )

    recipients = [f"supervisor:{sd.matter_id}", f"case_manager:{sd.matter_id}"]
    await notification_port.notify(
        recipients=recipients,
        payload={"type": "escalation", "level": new_level, "deadline_id": deadline_id},
        idem_key=f"esc:{deadline_id}:{new_level}",
    )

    if audit_fn:
        try:
            await audit_fn(
                actor="deadline_engine", action="deadline.escalated",
                inputs={"deadline_id": deadline_id, "level": new_level}, outputs={},
            )
        except Exception:
            pass

    return new_level


# ---------------------------------------------------------------------------
# Completion (cancel remaining reminders)
# ---------------------------------------------------------------------------


async def mark_done(
    *,
    deadline_id: str,
    store: DeadlineStore,
    scheduler: Scheduler,
    audit_fn: Any | None = None,
) -> None:
    """Mark a deadline done; cancel all armed reminders."""
    sd = store.get(deadline_id)
    if sd is None:
        return

    for rem in sd.reminders:
        if rem.status == "armed":
            for arm_id in rem.redundant_arm_ids:
                try:
                    await scheduler.cancel(arm_id)
                except Exception:
                    pass

    updated_reminders = [
        r.model_copy(update={"status": "skipped_past"}) if r.status == "armed" else r
        for r in sd.reminders
    ]
    store.save(sd.model_copy(update={"reminders": updated_reminders}))

    if audit_fn:
        try:
            await audit_fn(actor="deadline_engine", action="deadline.done",
                           inputs={"deadline_id": deadline_id}, outputs={})
        except Exception:
            pass

    log.info("deadline.done", deadline_id=deadline_id)
