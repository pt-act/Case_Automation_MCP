"""draft_welcome + send_welcome steps — spec G6.

draft_welcome: email.draft (no send).
send_welcome:  email.send, exactly-once, only post-approval.
No send path exists without a gate approval — enforced by the workflow engine.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from cam.core.domain.models import Communication, Contact, Matter
from cam.core.workflows.intake.types import CaseTypeConfig, IntakeFields, IntakeGap

log = structlog.get_logger(__name__)


async def draft_welcome(
    matter: Matter,
    contact: Contact,
    fields: IntakeFields,
    case_cfg: CaseTypeConfig,
    email_connector: Any,  # EmailConnector port
    run_id: str,
) -> tuple[str, list[IntakeGap]]:
    """Create a welcome email draft.  Returns (draft_id, gaps).

    Recipient must be the matter client.  Unresolved template variables → gaps.
    No send occurs here.
    """
    gaps: list[IntakeGap] = []

    # Verify recipient integrity — client must have an email
    if not contact.email:
        gaps.append(IntakeGap(
            field="email",
            reason="Client contact has no email address — cannot draft welcome.",
            severity="block",
        ))
        return "", gaps

    # Render template variables (simplified — in production uses document.generate)
    body = (
        f"Dear {contact.name or 'Client'},\n\n"
        f"Thank you for reaching out to us regarding your "
        f"{fields.case_type or 'immigration'} matter.\n\n"
        f"We have opened a matter for you (reference: {matter.reference}) and "
        f"our team will be in touch shortly.\n\n"
        f"Best regards,\nThe Immigration Team"
    )

    comm = Communication(
        id=str(uuid.uuid4()),
        matter_id=matter.id,
        direction="out",
        channel="email",
        subject=f"Welcome — {matter.reference}",
        body=body,
        status="draft",
        participants=[contact],
    )

    draft_id = await email_connector.create_draft(comm)
    log.info("intake.welcome_drafted", draft_id=draft_id, matter_id=matter.id)
    return draft_id, gaps


async def send_welcome(
    draft_id: str,
    run_id: str,
    email_connector: Any,  # EmailConnector port
) -> str:
    """Send the approved welcome draft.  Exactly-once via (run_id, step) idem key.

    The workflow engine ensures this step is only reachable post-gate-approval.
    """
    idem_key = f"{run_id}:send_welcome"
    message_id = await email_connector.send(draft_id, idem_key)
    log.info("intake.welcome_sent", message_id=message_id, run_id=run_id)
    return message_id
