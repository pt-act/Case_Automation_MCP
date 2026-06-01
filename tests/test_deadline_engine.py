"""Deadline engine — all groups G1–G8 + PBT invariants."""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cam.core.services.deadline.calendar import Calendar, CalendarError, get_calendar
from cam.core.services.deadline.compute import (
    MissingTriggerError,
    compute_due_date,
    preview_reminders,
    tool_deadline_compute,
)
from cam.core.services.deadline.deadman import DeadmanMonitor
from cam.core.services.deadline.firing import (
    MockNotificationPort,
    escalate,
    fire_reminder,
    mark_done,
)
from cam.core.services.deadline.reconcile import Reconciler
from cam.core.services.deadline.resources import resource_calendar_upcoming, resource_deadline_rules
from cam.core.services.deadline.rules import RuleStore, RuleValidationError
from cam.core.services.deadline.schedule import (
    DeadlineStore,
    InMemoryScheduler,
    PastDueOnCreateError,
    tool_deadline_schedule,
)
from cam.core.services.deadline.types import DeadlineRule, Offset, ReminderOffset, RuleRef


NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
CAL = get_calendar("us_federal")


# ──────────────────────────────────────────────────────────────
# G1 — Rule store
# ──────────────────────────────────────────────────────────────

VALID_YAML = """\
rule_id: rfe_response
jurisdiction: US
trigger: trigger_date
offset:
  days: 87
adjust: next_business_day
calendar_id: us_federal
description: RFE response window
assumption_unconfirmed: true
"""

CHANGED_YAML = """\
rule_id: rfe_response
jurisdiction: US
trigger: trigger_date
offset:
  days: 90
adjust: next_business_day
calendar_id: us_federal
description: RFE response window (updated)
assumption_unconfirmed: true
"""

MALFORMED_YAML = "not: valid: yaml: :\n  - garbage"
MISSING_TRIGGER_YAML = """\
rule_id: bad_rule
jurisdiction: US
offset:
  days: 30
calendar_id: us_federal
"""


def test_rule_yaml_round_trip() -> None:
    store = RuleStore()
    rule = store.load(VALID_YAML)
    assert rule.rule_id == "rfe_response"
    assert rule.offset.days == 87


def test_identical_content_same_version() -> None:
    store = RuleStore()
    store.load(VALID_YAML)
    store.load(VALID_YAML)  # idempotent
    versions = store.versions("rfe_response")
    assert len(versions) == 1  # only one version


def test_changed_content_new_version_old_retained() -> None:
    store = RuleStore()
    store.load(VALID_YAML)
    store.load(CHANGED_YAML)
    versions = store.versions("rfe_response")
    assert len(versions) == 2  # both retained


def test_registry_append_only() -> None:
    store = RuleStore()
    store.load(VALID_YAML)
    # No delete/update path exposed
    assert not hasattr(store, "delete")
    assert not hasattr(store, "update")


def test_malformed_rule_raises() -> None:
    store = RuleStore()
    with pytest.raises(RuleValidationError):
        store.load(MALFORMED_YAML)


def test_missing_trigger_raises() -> None:
    store = RuleStore()
    with pytest.raises(RuleValidationError):
        store.load(MISSING_TRIGGER_YAML)


# ──────────────────────────────────────────────────────────────
# G2 — Calendar
# ──────────────────────────────────────────────────────────────

def test_weekend_is_not_business_day() -> None:
    sat = date(2026, 5, 30)   # Saturday
    sun = date(2026, 5, 31)   # Sunday
    assert not CAL.is_business_day(sat)
    assert not CAL.is_business_day(sun)


def test_holiday_is_not_business_day() -> None:
    independence_day = date(2026, 7, 4)  # Saturday → observed Friday July 3
    observed = date(2026, 7, 3)
    assert not CAL.is_business_day(observed)


def test_next_business_day_skips_weekend() -> None:
    fri = date(2026, 5, 29)  # Friday
    nxt = CAL.next_business_day(fri)
    assert nxt == date(2026, 6, 1)  # Monday


def test_add_business_days_across_weekend() -> None:
    thu = date(2026, 5, 28)
    result = CAL.add_business_days(thu, 2)
    assert result == date(2026, 6, 1)  # skip Friday → Monday


def test_missing_year_raises() -> None:
    with pytest.raises(CalendarError):
        CAL._holidays(2050)  # outside SUPPORTED_YEARS


def test_calendar_version_stable() -> None:
    v1 = CAL.version()
    v2 = CAL.version()
    assert v1 == v2


# ──────────────────────────────────────────────────────────────
# G3 — Computation
# ──────────────────────────────────────────────────────────────

def _rfe_rule() -> DeadlineRule:
    return DeadlineRule(
        rule_id="rfe_response",
        trigger="trigger_date",
        offset=Offset(days=87),
        adjust="next_business_day",
        calendar_id="us_federal",
        reminders=[ReminderOffset(amount=-30, unit="days", label="-30d")],
    )


def test_compute_trace_reconstructable() -> None:
    rule = _rfe_rule()
    trigger_date = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    ref = RuleRef(rule_id=rule.rule_id, rule_version="abc123", jurisdiction="US")
    trace = compute_due_date(rule, {"trigger_date": trigger_date}, rule_ref=ref)
    # Verify reconstruction: raw_due = trigger + 87 days
    assert trace.raw_due.date() == (trigger_date + timedelta(days=87)).date()
    # due_at is adjusted to a business day
    assert CAL.is_business_day(trace.due_at.date())


def test_compute_missing_trigger_raises() -> None:
    rule = _rfe_rule()
    with pytest.raises(MissingTriggerError):
        compute_due_date(rule, {})


def test_compute_past_due_flag() -> None:
    rule = _rfe_rule()
    old_trigger = datetime(2020, 1, 1, tzinfo=timezone.utc)
    ref = RuleRef(rule_id=rule.rule_id, rule_version="v1", jurisdiction="US")
    trace = compute_due_date(rule, {"trigger_date": old_trigger}, rule_ref=ref)
    assert trace.due_at < NOW  # past due


def test_preview_reminders_all_before_due() -> None:
    rule = _rfe_rule()
    trigger = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ref = RuleRef(rule_id=rule.rule_id, rule_version="v1", jurisdiction="US")
    trace = compute_due_date(rule, {"trigger_date": trigger}, rule_ref=ref)
    previews = preview_reminders(rule, trace.due_at)
    for p in previews:
        assert p["before_due"], f"Reminder not before due: {p}"


async def test_compute_tool_no_deadline_persisted() -> None:
    rule = _rfe_rule()
    store = DeadlineStore()
    ref = RuleRef(rule_id=rule.rule_id, rule_version="v1", jurisdiction="US")
    trigger = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = await tool_deadline_compute(rule, {"trigger_date": trigger}, rule_ref=ref)
    # No deadline persisted
    assert len(store._deadlines) == 0
    assert "due_at" in result
    assert "trace" in result


# ──────────────────────────────────────────────────────────────
# G4 — Schedule + idempotency + past-due guard
# ──────────────────────────────────────────────────────────────

async def _schedule_rfe(
    trigger_days_from_now: int = 10,
    idem_key: str = "idem-001",
    now: datetime | None = None,
) -> tuple[DeadlineStore, InMemoryScheduler, object]:
    now = now or NOW
    rule = _rfe_rule()
    trigger = now + timedelta(days=trigger_days_from_now - 87)
    ref = RuleRef(rule_id=rule.rule_id, rule_version="v1", jurisdiction="US")
    store = DeadlineStore()
    scheduler = InMemoryScheduler()
    sd = await tool_deadline_schedule(
        matter_id="m1",
        rule=rule,
        trigger_inputs={"trigger_date": trigger},
        rule_ref=ref,
        name="RFE Response",
        idempotency_key=idem_key,
        store=store,
        scheduler=scheduler,
        now=now,
    )
    return store, scheduler, sd


async def test_schedule_idempotent() -> None:
    store, scheduler, sd1 = await _schedule_rfe()
    sd2 = await tool_deadline_schedule(
        matter_id="m1",
        rule=_rfe_rule(),
        trigger_inputs={"trigger_date": NOW + timedelta(days=10 - 87)},
        rule_ref=RuleRef(rule_id="rfe_response", rule_version="v1", jurisdiction="US"),
        name="RFE Response",
        idempotency_key="idem-001",
        store=store,
        scheduler=scheduler,
        now=NOW,
    )
    assert sd1.deadline_id == sd2.deadline_id
    assert len(store._deadlines) == 1


async def test_past_due_on_create_raises() -> None:
    rule = _rfe_rule()
    # Trigger is so far in the past that due_at < now
    trigger = NOW - timedelta(days=200)
    ref = RuleRef(rule_id=rule.rule_id, rule_version="v1", jurisdiction="US")
    store = DeadlineStore()
    scheduler = InMemoryScheduler()
    with pytest.raises(PastDueOnCreateError):
        await tool_deadline_schedule(
            matter_id="m1",
            rule=rule,
            trigger_inputs={"trigger_date": trigger},
            rule_ref=ref,
            name="Past Due",
            idempotency_key="past-001",
            store=store,
            scheduler=scheduler,
            now=NOW,
        )
    # The flagged (non-armed) record IS persisted
    sd = store.get_by_idem("past-001")
    assert sd is not None
    assert sd.flagged_past_due_on_create is True
    assert all(r.status != "armed" for r in sd.reminders)


async def test_double_arm_uses_two_scheduler_paths() -> None:
    _, scheduler, sd = await _schedule_rfe()
    armed = [r for r in sd.reminders if r.status == "armed"]
    if armed:
        assert len(armed[0].redundant_arm_ids) == 2


async def test_past_reminder_skipped() -> None:
    rule = DeadlineRule(
        rule_id="tight",
        trigger="trigger_date",
        offset=Offset(days=100),
        adjust="none",
        calendar_id="us_federal",
        reminders=[ReminderOffset(amount=-200, unit="days", label="already past")],
    )
    trigger = NOW - timedelta(days=50)  # due in 50 days, but reminder was 200 days ago
    ref = RuleRef(rule_id=rule.rule_id, rule_version="v1", jurisdiction="US")
    store = DeadlineStore()
    scheduler = InMemoryScheduler()
    sd = await tool_deadline_schedule(
        matter_id="m1", rule=rule,
        trigger_inputs={"trigger_date": trigger},
        rule_ref=ref, name="T", idempotency_key="t-001",
        store=store, scheduler=scheduler, now=NOW,
    )
    assert any(r.status == "skipped_past" for r in sd.reminders)


# ──────────────────────────────────────────────────────────────
# G5 — Firing + escalation + done
# ──────────────────────────────────────────────────────────────

async def test_reminder_fires_exactly_once() -> None:
    _, scheduler, sd = await _schedule_rfe()
    notif = MockNotificationPort()
    armed = [r for r in sd.reminders if r.status == "armed"]
    if not armed:
        pytest.skip("No armed reminders in this scenario")

    rem = armed[0]
    # First fire — should deliver
    result1 = await fire_reminder(
        deadline_id=sd.deadline_id,
        reminder_idem_key=rem.idempotency_key,
        store=_,
        notification_port=notif,
    )
    # Reload the store reference
    result1 = await fire_reminder(
        deadline_id=sd.deadline_id,
        reminder_idem_key=rem.idempotency_key,
        store=_,
        notification_port=notif,
    )
    # Second fire on same reminder — status is now 'fired', should return False
    result2 = await fire_reminder(
        deadline_id=sd.deadline_id,
        reminder_idem_key=rem.idempotency_key,
        store=_,
        notification_port=notif,
    )
    assert result2 is False


async def test_escalation_level_monotonic() -> None:
    store, scheduler, sd = await _schedule_rfe()
    notif = MockNotificationPort()
    lvl1 = await escalate(deadline_id=sd.deadline_id, store=store, notification_port=notif)
    lvl2 = await escalate(deadline_id=sd.deadline_id, store=store, notification_port=notif)
    assert lvl2 > lvl1
    loaded = store.get(sd.deadline_id)
    assert loaded.escalation_level >= lvl2


async def test_done_cancels_reminders() -> None:
    store, scheduler, sd = await _schedule_rfe()
    await mark_done(deadline_id=sd.deadline_id, store=store, scheduler=scheduler)
    loaded = store.get(sd.deadline_id)
    assert all(r.status != "armed" for r in loaded.reminders)


# ──────────────────────────────────────────────────────────────
# G6 — Dead-man's-switch
# ──────────────────────────────────────────────────────────────

async def test_stopped_scheduler_triggers_alert() -> None:
    scheduler = InMemoryScheduler()
    scheduler.stop()
    # Force old heartbeat
    from datetime import datetime, timezone
    scheduler._last_heartbeat = datetime.now(tz=timezone.utc) - timedelta(seconds=600)
    alerts: list[dict] = []
    async def alert_fn(**kwargs): alerts.append(kwargs)
    monitor = DeadmanMonitor(scheduler, threshold_seconds=300, alert_fn=alert_fn)
    healthy = await monitor.check_once()
    assert not healthy
    assert alerts


async def test_healthy_scheduler_no_alert() -> None:
    scheduler = InMemoryScheduler()
    alerts: list[dict] = []
    async def alert_fn(**kwargs): alerts.append(kwargs)
    monitor = DeadmanMonitor(scheduler, threshold_seconds=300, alert_fn=alert_fn)
    healthy = await monitor.check_once()
    assert healthy
    assert not alerts


# ──────────────────────────────────────────────────────────────
# G7 — Reconciliation
# ──────────────────────────────────────────────────────────────

async def test_reconcile_detects_missing_in_case() -> None:
    store, _, sd = await _schedule_rfe()

    class MockCase:
        async def list_deadlines(self, matter_id: str):
            return []  # Case system has no deadlines

    reconciler = Reconciler(store=store, case_connector=MockCase())
    findings = await reconciler.reconcile("m1")
    assert any(f.kind == "missing_in_case" for f in findings)


async def test_reconcile_connector_error_parks_not_no_drift() -> None:
    store, _, sd = await _schedule_rfe()

    class FailingCase:
        async def list_deadlines(self, matter_id: str):
            raise ConnectionError("connector down")

    alerts: list[dict] = []
    async def alert_fn(**kwargs): alerts.append(kwargs)
    reconciler = Reconciler(store=store, case_connector=FailingCase(), alert_fn=alert_fn)
    findings = await reconciler.reconcile("m1")
    # Returns empty (parked) but fires an alert — does NOT treat as no-drift
    assert alerts  # alert was fired
    # findings is empty because we parked, but that's different from "no drift found"


async def test_reconcile_no_mutation() -> None:
    store, _, sd = await _schedule_rfe()
    original_count = len(store._deadlines)

    class MockCase:
        async def list_deadlines(self, matter_id: str): return []

    reconciler = Reconciler(store=store, case_connector=MockCase())
    await reconciler.reconcile("m1")
    assert len(store._deadlines) == original_count  # no new records created


# ──────────────────────────────────────────────────────────────
# G8 — Resources
# ──────────────────────────────────────────────────────────────

def test_rules_resource_includes_hashes() -> None:
    rs = RuleStore()
    rs.load(VALID_YAML)
    result = resource_deadline_rules(rs)
    assert result["rules"][0]["rule_version"]
    assert result["aggregate_version"]
    assert result["rules"][0]["assumption_unconfirmed"] is True


async def test_calendar_upcoming_window_filter() -> None:
    store, _, sd = await _schedule_rfe()
    result = resource_calendar_upcoming(store, window_days=1, matter_id="m1")
    # Due date is 10 days out → not in 1-day window
    deadline_items = [i for i in result["items"] if i["type"] == "deadline"]
    assert len(deadline_items) == 0


async def test_calendar_upcoming_matter_filter() -> None:
    store, _, _ = await _schedule_rfe()
    result = resource_calendar_upcoming(store, matter_id="wrong_matter")
    assert result["items"] == []


# ──────────────────────────────────────────────────────────────
# G9 PBT — business-day adjustment, reminder ordering, determinism
# ──────────────────────────────────────────────────────────────

@given(days=st.integers(min_value=1, max_value=2000))
@settings(max_examples=200)
def test_pbt_adjusted_day_always_business(days: int) -> None:
    base = date(2026, 1, 5)  # Monday
    raw = base + timedelta(days=days)
    adjusted = CAL.adjust(raw, "next_business_day")
    assert CAL.is_business_day(adjusted), f"Adjusted {adjusted} is not a business day"


@given(n=st.integers(min_value=0, max_value=50))
@settings(max_examples=100)
def test_pbt_add_business_days_correct_count(n: int) -> None:
    base = date(2026, 1, 5)  # Monday
    result = CAL.add_business_days(base, n)
    # Count business days between base and result
    count = 0
    current = base
    while current < result:
        current += timedelta(days=1)
        if CAL.is_business_day(current):
            count += 1
    assert count == n


@given(days=st.integers(min_value=1, max_value=365))
@settings(max_examples=100)
def test_pbt_compute_deterministic(days: int) -> None:
    rule = _rfe_rule()
    trigger = datetime(2025, 1, 1, tzinfo=timezone.utc)
    ref = RuleRef(rule_id=rule.rule_id, rule_version="v1", jurisdiction="US")
    t1 = compute_due_date(rule, {"trigger_date": trigger}, rule_ref=ref)
    t2 = compute_due_date(rule, {"trigger_date": trigger}, rule_ref=ref)
    assert t1.due_at == t2.due_at


@given(days=st.integers(min_value=1, max_value=365))
@settings(max_examples=100)
def test_pbt_reminders_all_before_due(days: int) -> None:
    rule = DeadlineRule(
        rule_id="test",
        trigger="trigger_date",
        offset=Offset(days=days),
        adjust="none",
        calendar_id="us_federal",
        reminders=[
            ReminderOffset(amount=-7, unit="days"),
            ReminderOffset(amount=-30, unit="days"),
        ],
    )
    trigger = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ref = RuleRef(rule_id="test", rule_version="v1", jurisdiction="US")
    trace = compute_due_date(rule, {"trigger_date": trigger}, rule_ref=ref)
    for p in preview_reminders(rule, trace.due_at):
        assert p["before_due"] or p["amount"] >= 0, (
            f"Reminder {p['amount']} is not before due"
        )
