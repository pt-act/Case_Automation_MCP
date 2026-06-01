"""QC-local Pydantic v2 types — spec §3, §4.1.

These types are feature-local to qc-verification and not promoted to
platform-foundation unless other specs need them.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Core verdict / aggregate
# ---------------------------------------------------------------------------


class Verdict(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


class Aggregate(StrEnum):
    PASS = "pass"
    PASS_WITH_WARNINGS = "pass_with_warnings"
    BLOCK = "block"


class SkipReason(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    FIELD_ABSENT = "field_absent"
    DISABLED = "disabled"
    UNSUPPORTED_PACKET_KIND = "unsupported_packet_kind"


# ---------------------------------------------------------------------------
# Severity policy
# ---------------------------------------------------------------------------


class SeverityPolicy(BaseModel):
    """Declares which verdicts a check may emit.

    A check may not emit a verdict outside its declared policy.
    The registry enforces this (downgrading fail→warn is blocked).
    """

    allowed: frozenset[Verdict] = Field(
        default_factory=lambda: frozenset([Verdict.PASS, Verdict.WARN, Verdict.FAIL, Verdict.SKIPPED])
    )

    def allows(self, verdict: Verdict) -> bool:
        return verdict in self.allowed or verdict == Verdict.SKIPPED

    model_config = {"arbitrary_types_allowed": True}


# Default policies
FAIL_OR_PASS = SeverityPolicy(allowed=frozenset([Verdict.PASS, Verdict.FAIL, Verdict.SKIPPED]))
WARN_OR_FAIL_OR_PASS = SeverityPolicy(
    allowed=frozenset([Verdict.PASS, Verdict.WARN, Verdict.FAIL, Verdict.SKIPPED])
)


# ---------------------------------------------------------------------------
# Check result
# ---------------------------------------------------------------------------


class CheckResult(BaseModel):
    """Result from running one check."""

    check_id: str
    check_version: str = "1.0"
    verdict: Verdict
    reason: str = Field("", description="PII-redacted human-readable reason.")
    evidence: dict[str, Any] = Field(default_factory=dict, description="Structured, redacted.")
    severity_applied: str = ""
    duration_ms: float = 0.0
    skip_reason: SkipReason | None = None


class CheckDescriptor(BaseModel):
    """Self-description for a registered check (FR-17)."""

    check_id: str
    version: str
    description: str
    applies_to: list[str] = Field(default_factory=list, description="PacketKind names or ['all'].")
    required_inputs: list[str] = Field(default_factory=list)
    severity_policy: str = ""


# ---------------------------------------------------------------------------
# QCConfig + fingerprint
# ---------------------------------------------------------------------------


class QCConfig(BaseModel):
    """Effective configuration for a single qc.verify call."""

    # Extraction confidence thresholds
    extraction_warn_threshold: float = 0.80
    extraction_fail_floor: float = 0.50
    # Deadline horizon in days
    deadline_horizon_days: int = 3650
    deadline_tight_window_days: int = 7
    # Per-check enabled flags
    enabled_checks: dict[str, bool] = Field(default_factory=dict)
    # Timeout per check (ms)
    check_timeout_ms: float = 500.0


def compute_config_fingerprint(config: QCConfig) -> str:
    """SHA-256 of the canonical sorted-key JSON of the config."""
    canonical = json.dumps(config.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# QCReport
# ---------------------------------------------------------------------------


class QCReport(BaseModel):
    """Full result of a qc.verify call."""

    packet_id: str
    run_id: str | None
    results: list[CheckResult]
    aggregate: Aggregate
    checks_selected: list[str]
    checks_skipped: dict[str, str] = Field(
        default_factory=dict, description="check_id → SkipReason"
    )
    created_at: datetime
    config_fingerprint: str
