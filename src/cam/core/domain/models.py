"""Canonical Pydantic v2 domain model — PTD §4.

Single source of truth for all shared entity types.  Connectors map
vendor payloads ↔ these; the core never sees a vendor JSON blob.
Immigration-specific categories are expressed via config/rule data,
never hard-coded into these types (CONVENTIONS §5, §8).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

# ---------------------------------------------------------------------------
# Primary domain entities
# ---------------------------------------------------------------------------


class Contact(BaseModel):
    """A person involved in one or more matters (client, attorney, rep)."""

    id: str = Field(..., description="Internal canonical id.")
    source: str = Field(..., description="System that owns this record, e.g. 'crm', 'case'.")
    name: str = Field(..., description="Full legal name.")
    email: EmailStr | None = Field(None, description="Primary email address.")
    phone: str | None = Field(None, description="Primary phone number.")
    role: str | None = Field(None, description="Role in the firm's "
        "context, e.g. 'client', 'attorney'.")
    external_ids: dict[str, str] = Field(
        default_factory=dict[str, Any],
        description="Cross-system identifiers, e.g. {'crm': '...', 'case': '...'}.",
    )


class Deadline(BaseModel):
    """A date obligation tracked by the deadline engine."""

    id: str = Field(..., description="Internal canonical id.")
    matter_id: str = Field(..., description="Parent matter.")
    name: str = Field(..., description="Human-readable obligation name.")
    due_at: datetime = Field(..., description="UTC deadline instant.")
    rule_id: str | None = Field(None, description="Versioned rule that computed this date.")
    status: Literal["pending", "reminded", "done", "missed"] = Field(
        "pending", description="Lifecycle status of the deadline."
    )
    escalation_level: int = Field(0, description="Monotonically increasing escalation counter.")


class Matter(BaseModel):
    """A case / file / engagement (here: an immigration case)."""

    id: str = Field(..., description="Internal canonical id.")
    source: str = Field(..., description="System that owns this record.")
    reference: str = Field(..., description="Firm-assigned matter reference number.")
    title: str = Field(..., description="Short descriptive title.")
    status: str = Field(..., description="Current matter status "
        "string (vendor-specific values normalised).")
    practice_area: str | None = Field(None, description="Practice area / case type "
        "from the active domain pack.")
    client: Contact = Field(..., description="Primary client contact.")
    responsible: str | None = Field(None, description="Responsible attorney id or name.")
    opened_at: datetime = Field(..., description="UTC instant the matter was opened.")
    key_dates: list[Deadline] = Field(default_factory=list, description="Tracked deadlines "
        "for this matter.")
    external_ids: dict[str, str] = Field(
        default_factory=dict[str, Any],
        description="Cross-system identifiers.",
    )


class Document(BaseModel):
    """A stored document associated with a matter."""

    id: str = Field(..., description="Internal canonical id.")
    matter_id: str = Field(..., description="Parent matter.")
    name: str = Field(..., description="File name.")
    mime_type: str = Field(..., description="MIME type, e.g. 'application/pdf'.")
    uri: str = Field(..., description="Storage URI (object store or doc-system path).")
    classification: str | None = Field(
        None, description="Document class, e.g. 'engagement_letter', 'court_filing'."
    )
    version: int = Field(..., description="Monotonically increasing version counter.")
    privileged: bool = Field(
        True,
        description=(
            "Confidentiality flag — backing store for the canonical `restricted` "
            "accessor. Labelled per the active domain pack's RestrictionPolicy "
            "('Privileged' in the immigration pack, 'Client-Confidential' in "
            "consulting). Defaults True (fail-safe)."
        ),
    )
    checksum: str = Field(..., description="SHA-256 hex digest of the stored bytes.")
    created_at: datetime = Field(..., description="UTC creation instant.")

    @property
    def restricted(self) -> bool:
        """Canonical, domain-agnostic confidentiality flag.

        Backed by the `privileged` field (same column — no migration; decision
        D-6). `privileged` is retained as a permanent backward-compatible alias
        (decision D-3). New code should prefer `restricted`; the confidentiality
        gate keys on this value regardless of which name set it.
        """
        return self.privileged

    @restricted.setter
    def restricted(self, value: bool) -> None:
        self.privileged = value


class Communication(BaseModel):
    """An email or message, inbound or outbound."""

    id: str = Field(..., description="Internal canonical id.")
    matter_id: str | None = Field(None, description="Associated matter (None "
        "before matter is created).")
    direction: Literal["in", "out"] = Field(..., description="Message direction relative "
        "to the firm.")
    channel: str = Field(..., description="Delivery channel, e.g. 'email'.")
    subject: str | None = Field(None, description="Message subject line.")
    body: str = Field(..., description="Message body text.")
    status: str = Field(
        ..., description="Lifecycle status: 'draft' | 'pending_approval' | 'sent'."
    )
    participants: list[Contact] = Field(
        default_factory=list, description="All contacts on this communication."
    )


class Task(BaseModel):
    """An actionable task attached to a matter."""

    id: str = Field(..., description="Internal canonical id.")
    matter_id: str = Field(..., description="Parent matter.")
    title: str = Field(..., description="Task description.")
    assignee: str | None = Field(None, description="Assignee id or role.")
    due_at: datetime | None = Field(None, description="Optional due date (UTC).")
    status: str = Field(..., description="Task status string, e.g. 'open', 'done'.")


# ---------------------------------------------------------------------------
# Supporting / value types (proposed here for shared reuse)
# ---------------------------------------------------------------------------


class AuditRecord(BaseModel):
    """One entry in the append-only, hash-chained audit log."""

    id: int = Field(..., description="Monotonic PK / sequence — defines chain order.")
    actor: str = Field(..., description="Identity that performed the action.")
    action: str = Field(..., description="Action name, e.g. 'matter.create'.")
    inputs: dict[str, Any] = Field(
        default_factory=dict, description="PII-scrubbed action inputs (JSONB)."
    )
    outputs: dict[str, Any] = Field(
        default_factory=dict, description="PII-scrubbed action outputs (JSONB)."
    )
    approval: dict[str, Any] | None = Field(None, description="Gate outcome if "
        "this action required approval.")
    timestamp: datetime = Field(..., description="UTC server-sourced timestamp.")
    run_id: str | None = Field(None, description="Workflow run correlation id.")
    prev_hash: str = Field(..., description="SHA-256 hex of the "
        "previous record (zeros for genesis).")
    record_hash: str = Field(..., description="SHA-256 hex of prev_hash + canonical(this record).")


class FeatureFlag(BaseModel):
    """Per-workflow feature flag for wave-by-wave rollout."""

    key: str = Field(..., description="Workflow or feature key, e.g. 'intake'.")
    enabled: bool = Field(False, description="Whether the feature is enabled. Default: off.")


class ResidencyRegion(str):
    """Configurable data-residency region tag (open-ended string enum)."""

    US_EAST = "us-east-1"
    US_WEST = "us-west-2"
    EU_WEST = "eu-west-1"


# ACL placeholder — semantics owned by connector-framework / document-routing.
# Defined here only to reserve the name so downstream can reference it
# without forking the domain package.
class ACL(BaseModel):
    """Access-control descriptor for DocStoreConnector.move. Owned by connector-framework."""

    principals: list[str] = Field(default_factory=list, description="Principal ids with access.")
    permission: Literal["read", "write", "none"] = Field(
        "read", description="Permission level granted."
    )
    external: bool = Field(
        False, description="True if any principal is external to the firm."
    )
