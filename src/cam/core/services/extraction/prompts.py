"""extraction_schema prompt — versioned, data-driven by MappingProfile — spec §4.2.

The prompt instructs the LLM to:
- Map raw text to domain fields per the active profile
- Return per-field confidence in [0,1]
- Leave unknown fields absent (never guess/hallucinate)
- Emit output as a JSON array matching ExtractedField[]
"""

from __future__ import annotations

from cam.core.services.extraction.types import MappingProfile

PROMPT_VERSION = "v1"

_TEMPLATE = """\
# Extraction Task — {profile_name} v{profile_version} (prompt {prompt_version})

You are a structured-data extraction assistant for a US immigration law firm.
Extract fields from the provided document text and return them as a JSON array.

## Output format
Return ONLY a JSON array of objects with this exact shape:
  [
    {{
      "key": "<source_key>",
      "value": <extracted_value_or_null>,
      "raw_text": "<verbatim_text_you_read>",
      "confidence": <float_0_to_1>
    }},
    ...
  ]

## Rules
1. confidence MUST be a float in [0.0, 1.0].
2. If a field is not present in the text, OMIT it from the array entirely (do NOT include it with null).
3. Never fabricate values. If uncertain, lower the confidence; do not guess.
4. raw_text must be the verbatim text you extracted the value from.
5. Return ONLY the JSON array — no preamble, no explanation.

## Fields to extract
{field_list}

## Document text
{document_text_placeholder}
"""

_FIELD_LINE = "- key={key!r}  target_type={target_type}  sensitive={sensitive}"


def render_extraction_schema(profile: MappingProfile) -> str:
    """Render the extraction_schema prompt for a given mapping profile."""
    field_lines = "\n".join(
        _FIELD_LINE.format(
            key=fm.source_key,
            target_type=fm.target_type,
            sensitive=fm.sensitive,
        )
        for fm in profile.fields
    )
    return _TEMPLATE.format(
        profile_name=profile.name,
        profile_version=profile.version,
        prompt_version=PROMPT_VERSION,
        field_list=field_lines or "(no fields defined in profile)",
        document_text_placeholder="{{DOCUMENT_TEXT}}",
    )
