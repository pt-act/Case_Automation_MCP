"""Normalised inbound webhook Event model — spec §4.4."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Event(BaseModel):
    """Normalised inbound webhook event.

    The raw vendor payload is never stored in this model; only a
    PII-minimised projection is kept in `payload`.  `raw_ref` optionally
    points to a retained raw body in object storage.
    """

    id: str = Field(..., description="Internal event id (uuid).")
    connector: str = Field(..., description="Source connector name.")
    provider_event_id: str = Field(..., description="Vendor's event id — the dedup key.")
    type: str = Field(
        ...,
        description="Normalised event type, e.g. 'matter.status_changed', 'lead.created'.",
    )
    occurred_at: datetime = Field(..., description="When the event "
        "occurred (vendor-reported, UTC).")
    received_at: datetime = Field(..., description="When we received the webhook (UTC).")
    matter_ref: str | None = Field(
        None, description="External matter id/ref if resolvable from payload."
    )
    payload: dict = Field(
        default_factory=dict,
        description="Normalised, PII-minimised projection — NOT the raw vendor blob.",
    )
    raw_ref: str | None = Field(
        None,
        description="Pointer/hash to stored raw body if retention is enabled.",
    )
