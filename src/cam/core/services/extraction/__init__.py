"""Data extraction service — text + OCR + LLM structuring, proposes only."""

from cam.core.services.extraction.errors import (
    ExtractionError,
    InputLimitError,
    ProviderUnavailableError,
    ResidencyError,
    UnsupportedInputError,
)
from cam.core.services.extraction.pipeline import ExtractionService, tool_document_extract
from cam.core.services.extraction.types import (
    ExtractInput,
    ExtractedField,
    ExtractionProposal,
    ExtractionSource,
    FieldMapping,
    MappingProfile,
    PageCoverage,
)

__all__ = [
    "ExtractionError",
    "ExtractionProposal",
    "ExtractionService",
    "ExtractionSource",
    "ExtractInput",
    "ExtractedField",
    "FieldMapping",
    "InputLimitError",
    "MappingProfile",
    "PageCoverage",
    "ProviderUnavailableError",
    "ResidencyError",
    "UnsupportedInputError",
    "tool_document_extract",
]
