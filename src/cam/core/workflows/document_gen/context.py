"""Context resolution + gap detection + deterministic formatting — spec G2."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import structlog

from cam.core.domain.models import Matter
from cam.core.workflows.document_gen.types import Gap, TemplateSpec

log = structlog.get_logger(__name__)

# Deterministic date formatter (US style)
_DATE_FORMAT = "%B %d, %Y"


def format_value(value: Any, var_type: str) -> str:
    """Apply deterministic formatting based on variable type."""
    if value is None:
        return ""
    if var_type == "date":
        if isinstance(value, datetime):
            return value.strftime(_DATE_FORMAT)
        if isinstance(value, date):
            return value.strftime(_DATE_FORMAT)
        return str(value)
    if var_type == "bool":
        return "Yes" if value else "No"
    if var_type == "number":
        return str(value)
    return str(value)


def build_context(
    spec: TemplateSpec,
    matter: Matter,
    data_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve template variables from Matter/Contact + explicit overrides.

    Unknown override keys are logged (no PII) and ignored.
    Missing domain fields leave the variable unresolved (None), not blank.
    """
    resolved: dict[str, Any] = {}

    # Domain-derived defaults
    domain_map: dict[str, Any] = {
        "client.full_name": matter.client.name if matter.client else None,
        "client.email": str(matter.client.email) if (matter.client and matter.client.email) else
            None,
        "client.phone": matter.client.phone if matter.client else None,
        "matter.reference": matter.reference,
        "matter.title": matter.title,
        "matter.status": matter.status,
        "matter.practice_area": matter.practice_area,
        "matter.opened_at": matter.opened_at,
    }

    for var in spec.variables:
        domain_val = domain_map.get(var.name)
        resolved[var.name] = domain_val

    # Apply explicit overrides
    if data_context:
        for key, val in data_context.items():
            if key in {v.name for v in spec.variables}:
                resolved[key] = val
            else:
                log.debug("docgen.unknown_override_key", key=key)  # no value logged

    # Format values
    var_map = {v.name: v for v in spec.variables}
    return {
        name: format_value(val, var_map[name].type) if val is not None else None
        for name, val in resolved.items()
    }


def detect_gaps(
    spec: TemplateSpec,
    context: dict[str, Any],
) -> list[Gap]:
    """Find every required variable that is missing, empty, or invalid.

    Optional variables never produce gaps.  No silent coercion (FR-9).
    """
    gaps: list[Gap] = []
    for var in spec.variables:
        if not var.required:
            continue
        value = context.get(var.name)

        if value is None:
            gaps.append(Gap(variable=var.name, label=var.label, reason="missing"))
            continue

        str_val = str(value)
        if str_val.strip() == "":
            gaps.append(Gap(variable=var.name, label=var.label, reason="empty"))
            continue

        # Type-specific validation
        if var.type == "number":
            try:
                float(str_val)
            except ValueError:
                gaps.append(Gap(variable=var.name, label=var.label, reason="type_mismatch"))
                continue

        if var.type == "date":
            # Accept ISO date strings or formatted strings — just check non-empty
            pass  # non-empty already verified above

        if var.type == "enum" and var.enum_values:
            if str_val not in var.enum_values:
                gaps.append(Gap(variable=var.name, label=var.label, reason="enum_violation"))
                continue

    return gaps
