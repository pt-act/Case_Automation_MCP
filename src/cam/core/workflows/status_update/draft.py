"""Recipient resolution + prompt render + email.draft — spec G2.

Recipient set derived strictly from Matter participants.
No address outside matter.participants can be produced.
Missing client email → gap (no silent drop).
Draft creation precedes any send (enforced by step ordering).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from cam.core.domain.models import Communication, Contact, Matter
from cam.core.workflows.status_update.types import MatterStatusDelta

log = structlog.get_logger(__name__)

PROMPT_VERSION = "v1"

# ASSUMPTION (confirm): which roles receive status updates beyond the client
_DEFAULT_RECIPIENT_ROLES: frozenset[str] = frozenset({"client"})


def resolve_recipients(
    matter: Matter,
    allowed_roles: set[str] | None = None,
) -> tuple[list[Contact], list[str]]:
    """Derive recipient set from matter participants.

    Returns (recipients, gaps).
    A missing client email is a gap — not silently dropped.
    """
    gaps: list[str] = []
    recipients: list[Contact] = []

    # Client is always a recipient
    client = matter.client
    if not client.email:
        gaps.append(f"Client {client.id!r} has no email address.")
    else:
        recipients.append(client)

    return recipients, gaps


def render_status_update_body(delta: MatterStatusDelta, matter: Matter) -> str:
    """Render the status update email body from the delta (simplified prompt output).

    In production this would call an LLM client via the status_update_email prompt.
    The prompt version is recorded on the run context for audit.
    """
    client_name = matter.client.name if matter.client else "Client"
    return (
        f"Dear {client_name},\n\n"
        f"We are writing to inform you of a status update on your matter "
        f"(reference: {matter.reference}).\n\n"
        f"Status changed from '{delta.from_status}' to '{delta.to_status}'.\n\n"
        f"Please contact our office if you have any questions.\n\n"
        f"Best regards,\nThe Immigration Team"
    )


async def create_status_update_draft(
    delta: MatterStatusDelta,
    matter: Matter,
    recipients: list[Contact],
    email_connector: Any,  # EmailConnector port
    run_id: str,
) -> tuple[str, str]:
    """Create a draft Communication via the Email port.

    Returns (draft_comm_id, prompt_version_used).
    Risk tier: write (confirm) — no human gate at draft creation.
    """
    body = render_status_update_body(delta, matter)
    comm = Communication(
        id=str(uuid.uuid4()),
        matter_id=matter.id,
        direction="out",
        channel="email",
        subject=f"Status update — {matter.reference}",
        body=body,
        status="draft",
        participants=recipients,
    )
    draft_id = await email_connector.create_draft(comm)
    log.info(
        "status_update.draft_created",
        draft_id=draft_id,
        matter_id=matter.id,
        run_id=run_id,
    )
    return draft_id, PROMPT_VERSION
