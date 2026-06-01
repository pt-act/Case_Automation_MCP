"""dedupe_contact step — CRMConnector.find_contact + dedupe rules — spec G3."""

from __future__ import annotations

from typing import Any

from cam.core.workflows.intake.config import DedupeConfig, get_intake_config
from cam.core.workflows.intake.types import DedupeResult, IntakeFields, IntakeGap, MatchRef


async def dedupe_contact(
    fields: IntakeFields,
    crm: Any,  # CRMConnector port
    config: DedupeConfig | None = None,
) -> tuple[DedupeResult, list[IntakeGap]]:
    """Identify if this lead maps to an existing contact.

    Decision logic (in priority order):
      1. Exact email match → reuse_contact
      2. A-number exact match → reuse_contact
      3. Multiple conflicting candidates → ambiguous (never auto-merge)
      4. No match → new_contact

    Returns (DedupeResult, gaps) — ambiguous adds a blocking gap.
    """
    cfg = config or get_intake_config().dedupe
    gaps: list[IntakeGap] = []
    candidates: list[Any] = []

    # Email-first search
    if fields.email:
        try:
            email_candidates = await crm.find_contact(str(fields.email))
            candidates.extend(email_candidates)
        except Exception:
            pass

    # Name fallback
    if not candidates and fields.full_name:
        try:
            name_candidates = await crm.find_contact(fields.full_name)
            candidates.extend(name_candidates)
        except Exception:
            pass

    if not candidates:
        return DedupeResult(decision="new_contact", score=0.0), gaps

    # Exact email match → reuse
    if fields.email:
        exact = [c for c in candidates if str(getattr(c, "email", "") or "") == str(fields.email)]
        if len(exact) == 1:
            return DedupeResult(
                contact_match=MatchRef(contact_id=exact[0].id, source=exact[0].source, score=1.0),
                decision="reuse_contact",
                score=1.0,
            ), gaps

    # A-number exact match → reuse
    if fields.a_number:
        a_num_match = [
            c for c in candidates
            if c.external_ids.get("a_number") == fields.a_number
        ]
        if len(a_num_match) == 1:
            return DedupeResult(
                contact_match=MatchRef(contact_id=a_num_match[0].id, source=a_num_match[0].source, score=1.0),
                decision="reuse_contact",
                score=1.0,
            ), gaps

    # Multiple candidates without clear winner → ambiguous
    if len(candidates) > 1:
        gaps.append(IntakeGap(
            field="contact_id",
            reason=f"Found {len(candidates)} possible matching contacts — requires human disambiguation.",
            severity="block",
        ))
        return DedupeResult(decision="ambiguous", score=0.5), gaps

    # Single candidate but uncertain — still flag as ambiguous to be safe
    if candidates:
        gaps.append(IntakeGap(
            field="contact_id",
            reason="Found a possible matching contact but confidence is insufficient for automatic merge.",
            severity="block",
        ))
        return DedupeResult(
            contact_match=MatchRef(contact_id=candidates[0].id, source=candidates[0].source, score=0.6),
            decision="ambiguous",
            score=0.6,
        ), gaps

    return DedupeResult(decision="new_contact", score=0.0), gaps
