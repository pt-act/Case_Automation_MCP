"""ExtractionService — the full pipeline + `document.extract` MCP tool — spec §5, §4.1.

Pipeline steps (spec §5 happy path):
  1. Validate input / guards
  2. Text stage  (TextExtractor)
  3. OCR stage   (OcrEngine)
  4. Coverage    (build_coverage)
  5. Structuring (ResidencyGuard → LLMStructuringClient)  [if enabled + provider available]
  6. Mapping     (FieldMapper)
  7. Gating      (apply_threshold)
  8. Audit metadata record (no raw content / PII)
  9. Return ExtractionProposal

Risk tier: `read` — the service NEVER writes to a system of record.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import structlog

from cam.core.services.extraction import config as cfg
from cam.core.services.extraction.coverage import build_coverage, build_email_coverage
from cam.core.services.extraction.errors import (
    InputLimitError,
    ProviderUnavailableError,
    ResidencyError,
    UnsupportedInputError,
)
from cam.core.services.extraction.llm import (
    LLMStructuringClient,
    ResidencyGuard,
    normalise_fields,
)
from cam.core.services.extraction.mapper import FieldMapper, apply_threshold
from cam.core.services.extraction.ocr import OcrEngine, TesseractOcrEngine
from cam.core.services.extraction.text import PdfTextExtractor, TextExtractor
from cam.core.services.extraction.types import (
    ExtractInput,
    ExtractionProposal,
    ExtractionSource,
    ExtractedField,
    MappingProfile,
)

log = structlog.get_logger(__name__)

# Default mapping profile (no fields — caller must supply a profile with fields)
_DEFAULT_PROFILE = MappingProfile(name="default", version="1.0", fields=[])


class ExtractionService:
    """Orchestrates the full document.extract pipeline.

    Accepts injected extractors for testability (real implementations are
    the defaults; tests swap in mocks).
    """

    def __init__(
        self,
        text_extractor: TextExtractor | None = None,
        ocr_engine: OcrEngine | None = None,
        llm_client: Any | None = None,  # LLMStructuringClient | ResidencyGuard
        profiles: dict[str, MappingProfile] | None = None,
        audit_fn: Any | None = None,
    ) -> None:
        self._text = text_extractor or PdfTextExtractor()
        self._ocr = ocr_engine or TesseractOcrEngine()
        self._llm = llm_client  # None = structuring disabled
        self._profiles = profiles or {"default": _DEFAULT_PROFILE}
        self._audit = audit_fn
        self._mapper = FieldMapper()

    def extract(self, inp: ExtractInput, content: bytes) -> ExtractionProposal:
        """Run the full pipeline synchronously.  Never writes to a system of record."""
        warnings: list[str] = []
        run_id = str(uuid.uuid4())

        # ── 1. Validate ────────────────────────────────────────────────────
        if len(content) == 0:
            raise ValueError("Empty input content.")
        max_bytes = cfg.get_max_bytes()
        if len(content) > max_bytes:
            raise InputLimitError("bytes", max_bytes, len(content))

        profile = self._profiles.get(inp.mapping_profile or "default", _DEFAULT_PROFILE)
        threshold = inp.threshold if inp.threshold is not None else (
            profile.confidence_threshold
        )
        input_checksum = hashlib.sha256(content).hexdigest()

        # ── 2. Text stage ──────────────────────────────────────────────────
        if inp.content_kind == "email_body":
            email_text = content.decode("utf-8", errors="replace")
            page_texts: dict[int, str] = {}
            text_pages: list[int] = []
            ocr_needed: list[int] = []
            coverage = build_email_coverage()
            all_text = email_text
        else:
            max_pages = cfg.get_max_pages()
            page_texts, text_pages, ocr_needed = self._text.extract(content, inp.content_kind)
            total_pages = len(page_texts) if page_texts else 0
            if total_pages > max_pages:
                raise InputLimitError("pages", max_pages, total_pages)

            # ── 3. OCR stage ───────────────────────────────────────────────
            ocr_results: dict[int, tuple[str, float]] = {}
            if ocr_needed:
                ocr_results = self._ocr.ocr(content, ocr_needed)
                for p, (t, _) in ocr_results.items():
                    if not t.strip():
                        warnings.append(f"OCR returned empty text for page {p}.")

            # ── 4. Coverage ────────────────────────────────────────────────
            coverage, cov_warnings = build_coverage(
                total_pages=total_pages,
                text_pages=text_pages,
                ocr_results=ocr_results,
                attempted_ocr_pages=ocr_needed,
            )
            warnings.extend(cov_warnings)

            # Combine all text for structuring
            all_text_parts: list[str] = []
            for p in sorted(page_texts):
                all_text_parts.append(page_texts[p])
            for p, (t, _) in sorted(ocr_results.items()):
                if p not in page_texts:
                    all_text_parts.append(t)
            all_text = "\n".join(all_text_parts)

        # ── 5. Structuring ─────────────────────────────────────────────────
        structuring_status = "skipped"
        provider: str | None = None
        fields: list[ExtractedField] = []
        deterministic = True

        if inp.structuring and self._llm is not None and all_text.strip():
            try:
                from cam.core.services.extraction.prompts import render_extraction_schema
                prompt = render_extraction_schema(profile)
                raw_fields = self._llm.structure(all_text, profile, prompt)
                fields = normalise_fields(raw_fields, warnings)
                structuring_status = "ok"
                provider = getattr(self._llm, "provider_id", None)
                deterministic = False
            except ResidencyError as exc:
                log.warning("extraction.residency_denied", endpoint=str(exc.endpoint))
                structuring_status = "unavailable"
                warnings.append(f"Inference denied by residency policy: {exc.endpoint!r}")
                raise
            except ProviderUnavailableError:
                structuring_status = "unavailable"
                warnings.append("LLM provider unavailable; structuring skipped.")

        # ── 6. Mapping ─────────────────────────────────────────────────────
        if fields:
            fields = self._mapper.to_domain(fields, profile, warnings=warnings)

        # ── 7. Gating (threshold) ──────────────────────────────────────────
        fields = apply_threshold(fields, threshold, warnings=warnings)

        # ── 8. Audit metadata ──────────────────────────────────────────────
        self._write_audit(run_id, inp, input_checksum, len(fields), structuring_status)

        return ExtractionProposal(
            run_id=run_id,
            input_ref=inp.input_ref,
            input_checksum=input_checksum,
            fields=fields,
            page_coverage=coverage,
            structuring_status=structuring_status,
            provider=provider,
            deterministic=deterministic,
            warnings=warnings,
        )

    def _write_audit(
        self,
        run_id: str,
        inp: ExtractInput,
        checksum: str,
        field_count: int,
        status: str,
    ) -> None:
        if self._audit is None:
            return
        try:
            import asyncio
            coro = self._audit(
                actor="extraction_service",
                action="document.extract",
                inputs={
                    "input_ref": inp.input_ref,
                    "input_checksum": checksum,
                    "content_kind": inp.content_kind,
                    "mapping_profile": inp.mapping_profile,
                    "structuring": inp.structuring,
                },
                outputs={
                    "field_count": field_count,
                    "structuring_status": status,
                },
                run_id=run_id,
            )
            if asyncio.iscoroutine(coro):
                # Fire-and-forget in sync context — caller wires async if needed
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(coro)
                    else:
                        loop.run_until_complete(coro)
                except Exception:
                    pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# MCP tool entrypoint
# ---------------------------------------------------------------------------


async def tool_document_extract(
    inp: ExtractInput,
    content: bytes,
    service: ExtractionService,
) -> ExtractionProposal:
    """The `document.extract` MCP tool.

    Risk tier: read — no connector write reachable.
    Validates → runs pipeline → returns ExtractionProposal.
    """
    if inp.content_kind not in ("pdf", "image", "email_body"):
        raise UnsupportedInputError(f"Unknown content_kind {inp.content_kind!r}.")
    return service.extract(inp, content)
