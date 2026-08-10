"""deadline-rules:// and calendar://upcoming resources — spec §4.2, G8."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cam.core.services.deadline.rules import RuleStore
from cam.core.services.deadline.schedule import DeadlineStore


def resource_deadline_rules(rule_store: RuleStore, jurisdiction: str = "US") -> dict:
    """deadline-rules://{jurisdiction} — tamper-evident; assumption_unconfirmed flags included."""
    rules = rule_store.all_rules(jurisdiction)
    return {
        "jurisdiction": jurisdiction,
        "rules": [
            {
                "rule_id": rule_id,
                "rule_version": version,
                "description": rule.description,
                "assumption_unconfirmed": rule.assumption_unconfirmed,
                "trigger": rule.trigger,
                "adjust": rule.adjust,
                "calendar_id": rule.calendar_id,
            }
            for rule_id, version, rule in rules
        ],
        "aggregate_version": _aggregate_version([v for _, v, _ in rules]),
    }


def _aggregate_version(versions: list[str]) -> str:
    import hashlib
    return hashlib.sha256(",".join(sorted(versions)).encode()).hexdigest()[:12]


def resource_calendar_upcoming(
    deadline_store: DeadlineStore,
    window_days: int = 90,
    matter_id: str | None = None,
) -> dict:
    """calendar://upcoming — deadlines + pending reminders within window, ordered."""
    now = datetime.now(tz=UTC)
    cutoff = now + timedelta(days=window_days)

    items = []
    for sd in deadline_store._deadlines.values():
        if matter_id and sd.matter_id != matter_id:
            continue
        if now <= sd.trace.due_at <= cutoff:
            items.append({
                "type": "deadline",
                "deadline_id": sd.deadline_id,
                "matter_id": sd.matter_id,
                "due_at": sd.trace.due_at.isoformat(),
                "rule_id": sd.rule_ref.rule_id,
            })
        for rem in sd.reminders:
            if rem.status == "armed" and now <= rem.fire_at <= cutoff:
                items.append({
                    "type": "reminder",
                    "deadline_id": sd.deadline_id,
                    "matter_id": sd.matter_id,
                    "fire_at": rem.fire_at.isoformat(),
                    "label": rem.offset.label,
                })

    items.sort(key=lambda x: x.get("due_at") or x.get("fire_at", ""))
    return {"window_days": window_days, "matter_id": matter_id, "items": items}
