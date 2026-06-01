"""Intake-local Pydantic v2 types — spec §3.

Immigration specifics live here and in config, NOT in core Matter/Contact
(CONVENTIONS §5, §8). These types are persisted with the workflow run
for audit + resume, not as new system-of-record entities.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

_A_NUMBER_RE = re.compile(r"^A\d{8,9}$")


# ---------------------------------------------------------------------------
# Lead payload (raw inbound lead)
# ---------------------------------------------------------------------------


class LeadPayload(BaseModel):
    """The raw inbound lead — the workflow's starting input."""

    source_channel: Literal["email", "form", "manual", "api"] = "email"
    received_at: datetime
    raw: dict[str, Any] = Field(default_factory=dict)
    attachments: list[dict[str, str]] = Field(default_factory=list)
    provider_event_id: str | None = None


# ---------------------------------------------------------------------------
# IntakeFields — normalised, mapped capture
# ---------------------------------------------------------------------------


class IntakeFields(BaseModel):
    """Normalised lead capture.  Immigration specifics are data here, not in core."""

    # Bio
    full_name: str | None = None
    dob: str | None = None  # ISO date string
    preferred_language: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    address: str | None = None

    # Immigration
    country_of_origin: str | None = None
    a_number: str | None = None
    current_status: str | None = None
    case_type: str | None = None
    prior_filings: list[str] = Field(default_factory=list)
    lead_source: str | None = None
    description: str | None = None

    # Confidence per field (from extraction service)
    field_confidence: dict[str, float] = Field(default_factory=dict)

    @field_validator("a_number")
    @classmethod
    def _validate_a_number(cls, v: str | None) -> str | None:
        if v is not None and not _A_NUMBER_RE.match(v):
            raise ValueError(
                f"A-number must be 'A' followed by 8 or 9 digits; got {v!r}."
            )
        return v


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------


class IntakeGap(BaseModel):
    """A missing or low-confidence required field."""

    field: str
    reason: str
    severity: Literal["block", "warn"] = "warn"


# ---------------------------------------------------------------------------
# Dedupe result
# ---------------------------------------------------------------------------


class MatchRef(BaseModel):
    contact_id: str
    source: str
    score: float = 1.0


class DedupeResult(BaseModel):
    contact_match: MatchRef | None = None
    matter_match: MatchRef | None = None
    decision: Literal["reuse_contact", "new_contact", "ambiguous"] = "new_contact"
    score: float = 0.0


# ---------------------------------------------------------------------------
# Case-type config
# ---------------------------------------------------------------------------


class TaskTemplate(BaseModel):
    """Template for an opening task (rendered into a Task for each new matter)."""

    title: str
    assignee_role: str = "paralegal"
    due_offset_days: int | None = None
    sort: int = 0


class CaseTypeConfig(BaseModel):
    """Per-case-type configuration.  ASSUMPTION (confirm): full field set."""

    case_type: str
    required_fields: list[str] = Field(default_factory=list)
    opening_tasks: list[TaskTemplate] = Field(default_factory=list)
    deadline_rule_ids: list[str] = Field(default_factory=list)
    welcome_template_id: str = "default_welcome"
