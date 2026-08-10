"""Vendor-agnostic port protocols — PTD §6, spec §4.1.

Workflows depend on these protocols only.  Concrete adapters live in
`connectors/<vendor>/` and are never imported by workflows directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cam.connectors.webhook.models import Event

from datetime import datetime
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# MatterDraft — input to CaseConnector.create_matter
# Writable subset of Matter; no id/source (those are assigned by the vendor).
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field

from cam.core.domain.models import ACL, Communication, Contact, Deadline, Document, Matter


class MatterDraft(BaseModel):
    """Draft payload for creating a matter in a case-management system."""

    reference: str = Field(..., description="Firm-assigned reference number.")
    title: str = Field(..., description="Short descriptive title.")
    status: str = Field(..., description="Initial status string.")
    practice_area: str | None = Field(None, description="Practice area.")
    client: Contact = Field(..., description="Primary client contact.")
    responsible: str | None = Field(None, description="Responsible attorney id or name.")
    opened_at: datetime = Field(..., description="UTC opening instant.")
    external_ids: dict[str, str] = Field(
        default_factory=dict, description="Cross-system identifiers."
    )


# ---------------------------------------------------------------------------
# Port protocols (PTD §6 verbatim)
# ---------------------------------------------------------------------------


@runtime_checkable
class CaseConnector(Protocol):
    """Port for case / matter management systems."""

    async def get_matter(self, id: str) -> Matter: ...

    async def create_matter(self, data: MatterDraft) -> Matter: ...

    async def list_deadlines(self, matter_id: str) -> list[Deadline]: ...


@runtime_checkable
class CRMConnector(Protocol):
    """Port for CRM / contact systems."""

    async def upsert_contact(self, c: Contact) -> Contact: ...

    async def find_contact(self, q: str) -> list[Contact]: ...


@runtime_checkable
class EmailConnector(Protocol):
    """Port for email systems.  create_draft is draft-by-default; send is gated."""

    async def create_draft(self, c: Communication) -> str: ...
    """Returns the vendor draft id."""

    async def send(self, draft_id: str, idem_key: str) -> str: ...
    """Returns the vendor message id.  Idempotent on idem_key."""


@runtime_checkable
class DocStoreConnector(Protocol):
    """Port for document store systems."""

    async def put(self, doc: Document, content: bytes) -> Document: ...

    async def get(self, id: str) -> tuple[Document, bytes]: ...

    async def move(self, id: str, folder: str, acl: ACL) -> Document: ...


# ---------------------------------------------------------------------------
# TriggerSink — contract for workflow-orchestration to implement.
# ASSUMPTION (confirm): exact shape owned by workflow-orchestration.
# Defined here so connector-framework can type-check against it without a
# hard import of the orchestration package (which doesn't exist yet).
# ---------------------------------------------------------------------------


@runtime_checkable
class TriggerSink(Protocol):
    """Receives normalised Events and turns them into workflow run triggers."""

    async def enqueue(self, event: Event) -> str: ...
    """Returns an opaque trigger id.  Idempotent on event.id."""
