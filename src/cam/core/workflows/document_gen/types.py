"""Document generation types — TemplateSpec, Gap, GenerationResult — spec §3, §4.1."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TemplateVariable(BaseModel):
    """One declared variable in a template."""

    name: str = Field(..., description="Jinja2 variable path, e.g. 'client.full_name'.")
    label: str = Field(..., description="Human label for gap surfacing.")
    required: bool = Field(True)
    type: Literal["string", "date", "number", "bool", "address", "enum"]
    enum_values: list[str] | None = None
    source_hint: str | None = Field(None, description="Where the data normally comes from.")


class TemplateSpec(BaseModel):
    """Declared metadata for a document template."""

    name: str = Field(..., description="Unique template id — resource key.")
    version: int = Field(1, description="Template content version, pinned per render.")
    format: Literal["docx", "html"] = "docx"
    target_form_id: str | None = Field(None, description="e.g. 'I-130'. ASSUMPTION (confirm)")
    privileged_default: bool = Field(True, description="ASSUMPTION (confirm): default true.")
    variables: list[TemplateVariable] = Field(default_factory=list)
    checksum: str = Field("", description="SHA-256 of template source bytes.")


class Gap(BaseModel):
    """An unresolved required variable in a template."""

    variable: str
    label: str
    reason: Literal["missing", "empty", "type_mismatch", "enum_violation"]


class GenerationResult(BaseModel):
    """Full output of document.generate."""

    document: Any | None = Field(None, description="Stored Document; None while not ready.")
    status: Literal["ready", "incomplete", "blocked", "failed"]
    gaps: list[Gap] = Field(default_factory=list)
    qc_verdict: Literal["pass", "warn", "fail", "skipped"] = "skipped"
    template_name: str = ""
    template_version: int = 1
    idempotency_key: str = ""
    run_id: str = ""
    warnings: list[str] = Field(default_factory=list)
