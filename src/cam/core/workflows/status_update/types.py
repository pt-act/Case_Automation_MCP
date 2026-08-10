"""MatterStatusDelta + StatusUpdateRunState — spec G1.1.

ASSUMPTION (confirm): MatterStatusDelta may become a shared platform type if
client-intake and deadline-engine also key off status transitions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class MatterStatusDelta(BaseModel):
    """A detected status change on a matter — the idempotency anchor."""

    matter_id: str
    from_status: str
    to_status: str
    change_id: str = Field(..., description="Stable, deterministic id for this transition.")
    source: Literal["webhook", "sweep", "manual"] = "webhook"
    detected_at: datetime
    context: dict[str, Any] = Field(default_factory=dict[str, Any])


class StatusUpdateRunState(BaseModel):
    """Feature-local run context persisted by the orchestrator."""

    run_id: str
    delta: MatterStatusDelta
    draft_comm_id: str | None = None
    qc_result: str | None = None   # pass | warn | fail | skipped
    approval: dict[str, Any] | None = None
    send_result: str | None = None  # message_id or error
