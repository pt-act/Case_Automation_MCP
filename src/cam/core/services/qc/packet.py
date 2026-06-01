"""VerificationPacket and its members — spec §3, §4.1.

The packet is the unit QC checks receive.  `now` is required (no implicit
clock — determinism invariant, spec §6 QN-5).  `external_bound` is the
caller's claim; the privilege check re-derives and uses the stricter value.

PacketKind: ASSUMPTION (confirm) — the set below covers the spec examples.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from cam.core.domain.models import Communication, Contact, Deadline, Document, Matter
from cam.core.services.qc.types import QCConfig


class PacketKind(StrEnum):
    EMAIL_SEND = "email_send"
    DOCUMENT_GENERATE = "document_generate"
    DOCUMENT_ROUTE = "document_route"
    INTAKE_FINALIZE = "intake_finalize"
    GENERIC = "generic"  # catch-all for extension


class QCExtractedField(BaseModel):
    """A field in the packet from the extraction service (simplified view)."""

    id: str
    name: str
    value: Any = None
    confidence: float = 1.0
    source_doc_id: str | None = None


class TemplateBinding(BaseModel):
    """One template's variable resolution state."""

    template_name: str
    required_vars: list[str] = Field(default_factory=list)
    resolved_vars: dict[str, str | None] = Field(default_factory=dict)


class AttachmentRef(BaseModel):
    """A referenced attachment that QC must verify is present."""

    referenced_name: str
    expected_checksum: str | None = None
    expected_version: int | None = None


class VerificationPacket(BaseModel):
    """The unit of data passed to qc.verify.

    Callers assemble this from their workflow's artefacts.
    `now` must be injected — checks never call datetime.now() internally.
    """

    packet_id: str
    run_id: str | None = None
    kind: PacketKind

    matter: Matter

    documents: list[Document] = Field(default_factory=list)
    communications: list[Communication] = Field(default_factory=list)
    deadlines: list[Deadline] = Field(default_factory=list)

    extracted_fields: list[QCExtractedField] = Field(default_factory=list)
    intended_recipients: list[Contact] = Field(default_factory=list)
    template_bindings: list[TemplateBinding] = Field(default_factory=list)
    attachment_refs: list[AttachmentRef] = Field(default_factory=list)

    external_bound: bool = Field(
        False,
        description="Caller's claim whether this packet is destined for an external recipient.",
    )

    now: datetime = Field(..., description="Reference time — injected; never derived inside checks.")
    config_snapshot: QCConfig = Field(default_factory=QCConfig)
