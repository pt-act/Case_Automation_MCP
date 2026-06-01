"""intake_interview prompt — versioned, gap-driven — spec G7.3."""

from __future__ import annotations

from cam.core.workflows.intake.types import CaseTypeConfig, IntakeFields, IntakeGap

PROMPT_VERSION = "v1"

_TEMPLATE = """\
# Intake Interview — {case_type} (prompt {version})

You are completing an intake interview for a new US immigration client.
Ask ONLY for the fields listed below — do not ask for information already provided.

## Fields still needed (gaps)

{gap_lines}

## Rules
1. Ask one logical group at a time (bio info, then immigration info).
2. Never invent values.  If the client is unsure, record it as uncertain.
3. Emit answers as a JSON object mapping field names to values, e.g.:
   {{"full_name": "Ana Garcia", "dob": "1990-03-15"}}
4. If all gaps are resolved, respond with an empty JSON object: {{}}

## Already collected (do not re-ask)

{collected_lines}
"""

_GAP_LINE = "- **{field}** ({severity}): {reason}"
_COLLECTED_LINE = "- {field}: {value}"


def render_intake_interview(
    fields: IntakeFields,
    gaps: list[IntakeGap],
    case_cfg: CaseTypeConfig,
) -> str:
    """Render the intake_interview prompt for the current gaps."""
    blocking = [g for g in gaps if g.severity == "block"]
    warn = [g for g in gaps if g.severity == "warn"]
    ordered_gaps = blocking + warn

    if not ordered_gaps:
        return (
            f"# Intake Interview — {case_cfg.case_type} (prompt {PROMPT_VERSION})\n\n"
            "All required fields have been collected. No questions needed."
        )

    gap_lines = "\n".join(
        _GAP_LINE.format(field=g.field, severity=g.severity, reason=g.reason)
        for g in ordered_gaps
    )

    gap_field_names = {g.field for g in ordered_gaps}
    collected: list[str] = []
    for field_name, field_info in IntakeFields.model_fields.items():
        if field_name in ("field_confidence",):
            continue
        value = getattr(fields, field_name, None)
        if value is not None and field_name not in gap_field_names:
            collected.append(_COLLECTED_LINE.format(field=field_name, value=value))

    collected_lines = "\n".join(collected) if collected else "(nothing collected yet)"

    return _TEMPLATE.format(
        case_type=case_cfg.case_type,
        version=PROMPT_VERSION,
        gap_lines=gap_lines,
        collected_lines=collected_lines,
    )
