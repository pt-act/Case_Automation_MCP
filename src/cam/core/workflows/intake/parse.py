"""parse_lead step — call document.extract, map to IntakeFields — spec G2."""

from __future__ import annotations

from typing import Any

from cam.core.services.extraction.types import ExtractionProposal
from cam.core.workflows.intake.config import IntakeConfig, get_intake_config
from cam.core.workflows.intake.types import IntakeFields, IntakeGap, LeadPayload


def parse_lead(
    lead: LeadPayload,
    proposal: ExtractionProposal | None,
    case_type_hint: str | None,
    config: IntakeConfig | None = None,
) -> tuple[IntakeFields, list[IntakeGap]]:
    """Map raw lead + extraction proposal → IntakeFields + gaps.

    Immigration specifics stay in IntakeFields, not in core domain types.
    Every required field for the resolved case type that is missing or
    low-confidence produces an IntakeGap (mapping-completeness invariant).
    """
    cfg = config or get_intake_config()
    gaps: list[IntakeGap] = []
    confidence: dict[str, float] = {}

    # Start from raw fields
    raw = lead.raw or {}

    def _get(key: str, default: Any = None) -> Any:
        return raw.get(key, default)

    # Map from extraction proposal if available
    if proposal:
        for field in proposal.fields:
            confidence[field.key] = field.confidence
            # Map dotted domain keys to intake fields
            simple_key = field.key.split(".")[-1]
            if simple_key in IntakeFields.model_fields and raw.get(simple_key) is None:
                raw[simple_key] = field.value

    fields = IntakeFields(
        full_name=_get("full_name") or _get("name"),
        dob=_get("dob") or _get("date_of_birth"),
        preferred_language=_get("preferred_language"),
        email=_get("email"),
        phone=_get("phone"),
        address=_get("address"),
        country_of_origin=_get("country_of_origin"),
        a_number=_get("a_number"),
        current_status=_get("current_status"),
        case_type=_get("case_type") or case_type_hint,
        prior_filings=_get("prior_filings") or [],
        lead_source=_get("lead_source"),
        description=_get("description"),
        field_confidence=confidence,
    )

    # Resolve case_type — fall back to uncategorised
    resolved_case_type = fields.case_type or cfg.default_case_type
    if not fields.case_type:
        fields = fields.model_copy(update={"case_type": resolved_case_type})
        if case_type_hint is None:
            gaps.append(IntakeGap(
                field="case_type",
                reason="Case type could not be determined from lead data.",
                severity="warn",
            ))

    # Check required fields for the resolved case type
    case_cfg = cfg.get_case_type(resolved_case_type)
    for req_field in case_cfg.required_fields:
        value = getattr(fields, req_field, None)
        field_conf = confidence.get(req_field, 1.0)
        if value is None or str(value).strip() == "":
            gaps.append(IntakeGap(
                field=req_field,
                reason=f"Required field {req_field!r} is missing.",
                severity="block",
            ))
        elif field_conf < cfg.confidence_threshold:
            gaps.append(IntakeGap(
                field=req_field,
                reason=f"Field {req_field!r} confidence {field_conf:.2f} below threshold.",
                severity="warn",
            ))

    return fields, gaps
