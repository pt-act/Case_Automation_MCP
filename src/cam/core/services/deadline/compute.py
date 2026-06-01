"""Computation engine + deadline.compute MCP tool — spec §4.1, §5, G3.

Risk tier: read — no Deadline row persisted, only an audit record.
Every due_at is fully reconstructable from its ComputationTrace.
Missing trigger input → typed error, no invented date.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from cam.core.services.deadline.calendar import Calendar, get_calendar
from cam.core.services.deadline.types import (
    ComputationTrace,
    DeadlineRule,
    ENGINE_VERSION,
    Offset,
    ReminderOffset,
    RuleRef,
)


class MissingTriggerError(Exception):
    def __init__(self, trigger_field: str) -> None:
        self.trigger_field = trigger_field
        super().__init__(f"Missing required trigger input: {trigger_field!r}")


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


def _apply_offset(base: datetime, offset: Offset, cal: Calendar) -> datetime:
    """Apply an offset to a datetime, advancing through business days where specified."""
    result = base

    # Calendar days / weeks / months / years (naive arithmetic)
    if offset.years:
        try:
            result = result.replace(year=result.year + offset.years)
        except ValueError:
            # Feb 29 → Feb 28 in non-leap year
            result = result.replace(year=result.year + offset.years, day=28)
    if offset.months:
        month = result.month + offset.months
        year = result.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        try:
            result = result.replace(year=year, month=month)
        except ValueError:
            result = result.replace(year=year, month=month, day=28)
    if offset.weeks:
        result += timedelta(weeks=offset.weeks)
    if offset.days:
        result += timedelta(days=offset.days)

    # Business days (use the calendar)
    if offset.business_days:
        result_date = cal.add_business_days(result.date(), offset.business_days)
        result = datetime.combine(result_date, result.time(), tzinfo=result.tzinfo)

    return result


def compute_due_date(
    rule: DeadlineRule,
    trigger_inputs: dict[str, datetime],
    as_of: datetime | None = None,
    rule_ref: RuleRef | None = None,
) -> ComputationTrace:
    """Compute a due date from a rule and trigger inputs.

    Returns a fully auditable ComputationTrace.
    Raises MissingTriggerError if the trigger field is absent.
    """
    trigger_value = trigger_inputs.get(rule.trigger)
    if trigger_value is None:
        raise MissingTriggerError(rule.trigger)

    cal = get_calendar(rule.calendar_id)
    raw_due = _apply_offset(trigger_value, rule.offset, cal)
    adjusted_date = cal.adjust(raw_due.date(), rule.adjust)
    due_at = datetime.combine(adjusted_date, raw_due.time(), tzinfo=raw_due.tzinfo)

    if rule_ref is None:
        rule_ref = RuleRef(rule_id=rule.rule_id, rule_version="unknown",
                           jurisdiction=rule.jurisdiction)

    return ComputationTrace(
        rule_ref=rule_ref,
        trigger_field=rule.trigger,
        trigger_value=trigger_value,
        offset=rule.offset,
        raw_due=raw_due,
        calendar_id=rule.calendar_id,
        calendar_version=cal.version(),
        adjustment=rule.adjust,
        due_at=due_at,
        computed_at=as_of or datetime.now(tz=timezone.utc),
        engine_version=ENGINE_VERSION,
    )


def preview_reminders(rule: DeadlineRule, due_at: datetime) -> list[dict]:
    """Compute preview fire_at for each reminder offset."""
    cal = get_calendar(rule.calendar_id)
    previews = []
    for rem in rule.reminders:
        if rem.unit == "business_days":
            fire_date = cal.add_business_days(due_at.date(), rem.amount)
        else:
            fire_date = (due_at + timedelta(days=rem.amount)).date()
        fire_at = datetime.combine(fire_date, due_at.time(), tzinfo=due_at.tzinfo)
        previews.append({
            "label": rem.label,
            "amount": rem.amount,
            "unit": rem.unit,
            "fire_at": fire_at.isoformat(),
            "before_due": fire_at < due_at,
        })
    return previews


# ---------------------------------------------------------------------------
# MCP tool — deadline.compute (risk tier: read)
# ---------------------------------------------------------------------------


async def tool_deadline_compute(
    rule: DeadlineRule,
    trigger_inputs: dict[str, datetime],
    rule_ref: RuleRef | None = None,
    as_of: datetime | None = None,
    audit_fn: Any | None = None,
) -> dict:
    """deadline.compute tool — pure read, no Deadline persisted.

    Returns: {due_at, trace, reminders_preview, past_due_flag}
    Writes one metadata audit record.
    """
    now = as_of or datetime.now(tz=timezone.utc)
    trace = compute_due_date(rule, trigger_inputs, as_of=now, rule_ref=rule_ref)
    past_due_flag = trace.due_at < now
    reminders_preview = preview_reminders(rule, trace.due_at)

    if audit_fn:
        try:
            await audit_fn(
                actor="deadline_engine",
                action="deadline.compute",
                inputs={
                    "rule_id": rule.rule_id,
                    "trigger_field": rule.trigger,
                    "jurisdiction": rule.jurisdiction,
                },
                outputs={
                    "due_at": trace.due_at.isoformat(),
                    "past_due_flag": past_due_flag,
                    "engine_version": ENGINE_VERSION,
                },
            )
        except Exception:
            pass

    return {
        "due_at": trace.due_at,
        "trace": trace,
        "reminders_preview": reminders_preview,
        "past_due_flag": past_due_flag,
    }
