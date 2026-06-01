"""Pydantic v2 domain types for the deadline engine — spec §3.

All types are feature-local.  `Deadline` itself is owned by platform-foundation;
this module defines the engine-specific wrapper types that reference it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ENGINE_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Rule reference (immutable pointer to a versioned rule)
# ---------------------------------------------------------------------------


class RuleRef(BaseModel):
    rule_id: str = Field(..., description="Logical rule identifier.")
    rule_version: str = Field(..., description="Content hash of the rule definition (immutable).")
    jurisdiction: str = Field("US", description="e.g. 'US'")
    practice_area: str | None = None


# ---------------------------------------------------------------------------
# Rule definition (parsed from YAML)
# ---------------------------------------------------------------------------


class Offset(BaseModel):
    """Date offset — multiple units may be combined."""
    days: int = 0
    business_days: int = 0
    weeks: int = 0
    months: int = 0
    years: int = 0


class ReminderOffset(BaseModel):
    amount: int = Field(..., description="Negative = before due date.")
    unit: Literal["days", "business_days"] = "days"
    label: str | None = None


class EscalationLevel(BaseModel):
    trigger: Literal["reminder_missed", "approaching", "after_due"]
    after: ReminderOffset | None = None
    recipients: list[str] = Field(default_factory=list, description="Role refs.")


class EscalationPolicy(BaseModel):
    levels: list[EscalationLevel] = Field(default_factory=list)


class DeadlineRule(BaseModel):
    """A single parsed, validated deadline rule."""

    rule_id: str
    jurisdiction: str = "US"
    practice_area: str | None = None
    trigger: str = Field(..., description="Name of the trigger date field.")
    offset: Offset = Field(default_factory=Offset)
    adjust: Literal["next_business_day", "previous_business_day", "none"] = "next_business_day"
    calendar_id: str = "us_federal"
    reminders: list[ReminderOffset] = Field(default_factory=list)
    escalation: EscalationPolicy = Field(default_factory=EscalationPolicy)
    description: str = ""
    assumption_unconfirmed: bool = True


# ---------------------------------------------------------------------------
# Computation trace (auditable derivation of a due date)
# ---------------------------------------------------------------------------


class ComputationTrace(BaseModel):
    """Full auditable derivation — every field needed to reconstruct the date."""

    rule_ref: RuleRef
    trigger_field: str
    trigger_value: datetime
    offset: Offset
    raw_due: datetime
    calendar_id: str
    calendar_version: str
    adjustment: str
    due_at: datetime
    computed_at: datetime
    engine_version: str = ENGINE_VERSION


# ---------------------------------------------------------------------------
# Scheduled deadline & reminders
# ---------------------------------------------------------------------------


class ScheduledReminder(BaseModel):
    id: str
    deadline_id: str
    fire_at: datetime
    offset: ReminderOffset
    status: Literal["armed", "fired", "missed", "skipped_past"] = "armed"
    idempotency_key: str
    redundant_arm_ids: list[str] = Field(default_factory=list)


class ScheduledDeadline(BaseModel):
    """The engine's persisted record for a deadline."""

    deadline_id: str
    matter_id: str
    rule_ref: RuleRef
    trace: ComputationTrace
    reminders: list[ScheduledReminder] = Field(default_factory=list)
    flagged_past_due_on_create: bool = False
    idempotency_key: str = ""
    escalation_level: int = 0


# ---------------------------------------------------------------------------
# Drift finding (reconciliation)
# ---------------------------------------------------------------------------


class Drift(BaseModel):
    matter_id: str
    deadline_id: str | None = None
    kind: Literal["missing_in_engine", "missing_in_case", "divergent_due_at", "divergent_status"]
    detail: str = ""
    detected_at: datetime
