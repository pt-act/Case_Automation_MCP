"""Pydantic v2 types for the data-extraction service — spec §3, §4.1.

All types are feature-local; they are not promoted to platform-foundation
because they are specific to extraction and not reused elsewhere (CONVENTIONS §5).
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Source provenance
# ---------------------------------------------------------------------------


class ExtractionSource(BaseModel):
    """Where a field value came from."""

    page: int | None = Field(None, description="1-based page index, None for email body.")
    method: Literal["text", "ocr", "llm"] = Field(
        ..., description="How this value was obtained."
    )
    span: str | None = Field(None, description="Optional char range / bbox reference.")


# ---------------------------------------------------------------------------
# Field mapping profile (data-driven; no hard-coded form logic)
# ---------------------------------------------------------------------------


class FieldMapping(BaseModel):
    """One entry in a mapping profile."""

    source_key: str = Field(..., description="Key the LLM returns.")
    domain_key: str = Field(
        ..., description="Dotted domain path, e.g. 'contact.email'."
    )
    target_type: Literal[
        "Contact", "Matter", "Document", "Deadline", "Communication", "Task"
    ] = Field(..., description="Domain type this field belongs to.")
    sensitive: bool = Field(
        False,
        description="If True, requires_verification is always True regardless of confidence.",
    )


class MappingProfile(BaseModel):
    """A named, versioned set of field mappings."""

    name: str = Field(..., description="Profile name, e.g. 'default', 'i130'.")
    version: str = Field("1.0", description="Semver; bump on breaking changes.")
    fields: list[FieldMapping] = Field(default_factory=list)
    confidence_threshold: float = Field(
        0.80,
        ge=0.0,
        le=1.0,
        description="Default confidence threshold for this profile.",
    )

    def mapping_by_source(self) -> dict[str, FieldMapping]:
        return {m.source_key: m for m in self.fields}


# ---------------------------------------------------------------------------
# Extracted field
# ---------------------------------------------------------------------------


class ExtractedField(BaseModel):
    """A single extracted field with provenance and confidence."""

    key: str = Field(..., description="Dotted domain path, e.g. 'contact.email'.")
    target_type: Literal[
        "Contact", "Matter", "Document", "Deadline", "Communication", "Task"
    ] = Field(..., description="Domain type.")
    value: Any = Field(None, description="Parsed value (str/date/number/...).")
    raw_text: str | None = Field(None, description="The literal text it was read from.")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Per-field confidence in [0, 1]."
    )
    requires_verification: bool = Field(
        False, description="True when confidence < threshold or rule-forced."
    )
    sources: list[ExtractionSource] = Field(
        default_factory=list, description="Provenance (>=1 when value present)."
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, v: Any) -> float:
        """Clamp out-of-range or NaN values to [0, 1]."""
        try:
            f = float(v)
            if math.isnan(f) or math.isinf(f):
                return 0.0
            return max(0.0, min(1.0, f))
        except (TypeError, ValueError):
            return 0.0


# ---------------------------------------------------------------------------
# Page coverage
# ---------------------------------------------------------------------------


class PageCoverage(BaseModel):
    """Which pages were processed by which method.

    INVARIANT: sorted(text_pages ∪ ocr_pages ∪ skipped_pages) == [1..total_pages]
    and the three sets are pairwise disjoint.
    """

    total_pages: int = Field(..., ge=0)
    text_pages: list[int] = Field(default_factory=list)
    ocr_pages: list[int] = Field(default_factory=list)
    skipped_pages: list[int] = Field(default_factory=list)

    def union(self) -> set[int]:
        return set(self.text_pages) | set(self.ocr_pages) | set(self.skipped_pages)

    def is_complete(self) -> bool:
        """True when every page 1..total_pages is accounted for."""
        if self.total_pages == 0:
            return True
        return self.union() == set(range(1, self.total_pages + 1))

    def is_disjoint(self) -> bool:
        t, o, s = set(self.text_pages), set(self.ocr_pages), set(self.skipped_pages)
        return not (t & o) and not (t & s) and not (o & s)


# ---------------------------------------------------------------------------
# Extraction proposal (the tool's output)
# ---------------------------------------------------------------------------


class ExtractionProposal(BaseModel):
    """The full result returned by `document.extract`.

    Never written to a system of record — the caller decides what to do with it.
    """

    run_id: str = Field(..., description="Extraction run id.")
    input_ref: str = Field(..., description="Document id / URI / email message ref.")
    input_checksum: str = Field(..., description="SHA-256 hex digest of input bytes.")
    fields: list[ExtractedField] = Field(default_factory=list)
    page_coverage: PageCoverage
    structuring_status: Literal["ok", "unavailable", "skipped"] = Field(
        "skipped", description="LLM structuring stage outcome."
    )
    provider: str | None = Field(None, description="Provider id used.")
    deterministic: bool = Field(
        True, description="True only when no LLM path was used."
    )
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tool input
# ---------------------------------------------------------------------------


class ExtractInput(BaseModel):
    """Input to the `document.extract` MCP tool."""

    input_ref: str = Field(..., description="Document id/URI or email message ref.")
    content_kind: Literal["pdf", "image", "email_body"] = Field(
        ..., description="Format of the input."
    )
    mapping_profile: str | None = Field(
        None, description="Which field-mapping profile to apply."
    )
    threshold: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Override default confidence threshold; must be in [0, 1].",
    )
    structuring: bool = Field(True, description="Allow LLM structuring stage.")
    async_ok: bool = Field(True, description="Permit async job for heavy inputs.")
