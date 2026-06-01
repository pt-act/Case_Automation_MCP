"""Domain mapping + confidence gating — spec §5 steps 6-7, §4.3 FieldMapper.

Mapping is data-driven via MappingProfile — no hard-coded form logic.
Unmapped / hallucinated keys are dropped with a warning.
Sensitive keys are always flagged regardless of confidence.
"""

from __future__ import annotations

import structlog

from cam.core.services.extraction.types import ExtractedField, MappingProfile

log = structlog.get_logger(__name__)


class FieldMapper:
    """Maps raw LLM-returned fields onto domain paths using a MappingProfile."""

    def to_domain(
        self,
        fields: list[ExtractedField],
        profile: MappingProfile,
        *,
        warnings: list[str] | None = None,
    ) -> list[ExtractedField]:
        """Apply the profile mappings; drop unmapped keys; warn on drops."""
        warnings = warnings if warnings is not None else []
        mapping = profile.mapping_by_source()
        result: list[ExtractedField] = []

        for field in fields:
            rule = mapping.get(field.key)
            if rule is None:
                warnings.append(
                    f"Unmapped key {field.key!r} dropped (not in profile {profile.name!r})."
                )
                log.debug("extraction.field_dropped", key=field.key, profile=profile.name)
                continue

            mapped = field.model_copy(
                update={
                    "key": rule.domain_key,
                    "target_type": rule.target_type,
                }
            )
            result.append(mapped)

        return result


def apply_threshold(
    fields: list[ExtractedField],
    threshold: float,
    *,
    warnings: list[str] | None = None,
) -> list[ExtractedField]:
    """Set `requires_verification` for fields below the threshold.

    Also respects the sensitive flag from the mapping profile (already baked
    into the field by the mapper or by rule-forced logic in the pipeline).

    This function is the *only* place that sets requires_verification based
    on the threshold; sensitive-key forcing happens in the pipeline.
    """
    result = []
    for field in fields:
        below = field.confidence < threshold
        flagged = field.requires_verification or below
        if below and not field.requires_verification:
            result.append(field.model_copy(update={"requires_verification": True}))
        else:
            result.append(field)
    return result
