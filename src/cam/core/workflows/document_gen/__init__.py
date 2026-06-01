"""Document generation — templates → versioned DOCX/PDF, QC-gated, stored."""

from cam.core.workflows.document_gen.template_store import (
    TemplateNotFound,
    TemplateStore,
    TemplateValidationError,
    make_simple_template,
)
from cam.core.workflows.document_gen.types import Gap, GenerationResult, TemplateSpec, TemplateVariable
from cam.core.workflows.document_gen.version_store import InMemoryVersionLedger
from cam.core.workflows.document_gen.tools import tool_document_generate, tool_form_prefill

__all__ = [
    "Gap",
    "GenerationResult",
    "InMemoryVersionLedger",
    "TemplateNotFound",
    "TemplateSpec",
    "TemplateStore",
    "TemplateValidationError",
    "TemplateVariable",
    "make_simple_template",
    "tool_document_generate",
    "tool_form_prefill",
]
