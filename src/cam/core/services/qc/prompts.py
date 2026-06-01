"""qc_checklist prompt — versioned reasoning scaffold — spec §4.2, G7."""

from __future__ import annotations

from cam.core.services.qc.registry import applicable_checks

PROMPT_VERSION = "v1"

_TEMPLATE = """\
# QC Checklist — {packet_kind} (prompt {version})

You are reviewing a verification packet of kind **{packet_kind}** before it proceeds.

## Rule
**Any single FAIL verdict blocks the packet.** The packet cannot proceed until all
FAIL verdicts are resolved.  WARN verdicts are informational.

## Applicable checks for this packet kind

{check_lines}

## How to use this checklist
1. For each check, review the evidence in the QCReport.
2. If a check FAILED, describe the specific issue to the approver.
3. PASS_WITH_WARNINGS means the packet can proceed but the approver should note the warnings.
4. BLOCK means the packet cannot proceed — identify which check(s) failed and why.

## No client data in this prompt
This is a structural reasoning scaffold.  Do not include any client names,
case references, document contents, or PII in your response.
"""

_CHECK_LINE = "- **{check_id}** (v{version}): {description}"


def render_qc_checklist(packet_kind: str) -> str:
    """Render the qc_checklist prompt for a given packet kind."""
    checks = applicable_checks(packet_kind)
    if not checks:
        check_lines = "(No checks registered for this packet kind.)"
    else:
        check_lines = "\n".join(
            _CHECK_LINE.format(
                check_id=c.id,
                version=c.version,
                description=c.describe().description,
            )
            for c in checks
        )
    return _TEMPLATE.format(
        packet_kind=packet_kind,
        version=PROMPT_VERSION,
        check_lines=check_lines,
    )
