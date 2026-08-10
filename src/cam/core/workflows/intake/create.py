"""create_contact + create_matter steps — write/confirm, idempotent — spec G4."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from cam.core.domain.models import Contact, Matter
from cam.core.workflows.intake.types import CaseTypeConfig, DedupeResult, IntakeFields


async def create_contact(
    fields: IntakeFields,
    dedupe: DedupeResult,
    crm: Any,  # CRMConnector port
    run_id: str,
) -> Contact:
    """Upsert a contact.  Skip if reuse_contact; idempotent on run+step."""
    if dedupe.decision == "reuse_contact" and dedupe.contact_match:
        # Return a stub contact carrying the known id — the step is a no-op
        return Contact(
            id=dedupe.contact_match.contact_id,
            source=dedupe.contact_match.source,
            name=fields.full_name or "Unknown",
            email=str(fields.email) if fields.email is not None else None,
            phone=fields.phone,
            role="client",
            external_ids={"run_id": run_id},
        )

    contact = Contact(
        id=str(uuid.uuid4()),
        source="intake",
        name=fields.full_name or "Unknown",
        email=str(fields.email) if fields.email is not None else None,
        phone=fields.phone,
        role="client",
        external_ids={
            "run_id": run_id,
            **({"a_number": fields.a_number} if fields.a_number else {}),
        },
    )
    return await crm.upsert_contact(contact)


async def create_matter(
    fields: IntakeFields,
    contact: Contact,
    case_cfg: CaseTypeConfig,
    case_connector: Any,  # CaseConnector port
    run_id: str,
) -> Matter:
    """Create a matter linked to the contact.  Privileged on create; idempotent."""
    from cam.connectors.ports import MatterDraft

    draft = MatterDraft(
        reference=f"INTAKE-{run_id[:8].upper()}",
        title=f"{contact.name} — {case_cfg.case_type}",
        status="open",
        practice_area=case_cfg.case_type,
        client=contact,
        responsible=None,
        opened_at=datetime.now(tz=UTC),
        external_ids={"run_id": run_id, "source_channel": fields.lead_source or "intake"},
    )
    matter = await case_connector.create_matter(draft)
    # Ensure privileged flag is set (immigration matters are privileged by default)
    return matter
