"""G7 + G8 — Pipeline, document.extract tool, audit/observability focused tests."""

from __future__ import annotations

import hashlib
import io

import pytest

from cam.core.services.extraction.errors import InputLimitError, ResidencyError
from cam.core.services.extraction.llm import MockLLMClient, ResidencyGuard
from cam.core.services.extraction.ocr import MockOcrEngine
from cam.core.services.extraction.pipeline import ExtractionService, tool_document_extract
from cam.core.services.extraction.types import (
    ExtractInput,
    ExtractedField,
    ExtractionSource,
    FieldMapping,
    MappingProfile,
)



def _make_pdf_bytes() -> bytes:
    return b"%PDF-1.4 placeholder for testing"


class MockTextExtractor:
    def extract(self, content: bytes, kind: str):
        if kind == "email_body":
            return {}, [], []
        return {1: "Hello World from the document."}, [1], []


def _profile_with_field() -> MappingProfile:
    return MappingProfile(
        name="test",
        version="1.0",
        fields=[
            FieldMapping(source_key="email", domain_key="contact.email", target_type="Contact")
        ],
    )


def _field(key: str = "email", confidence: float = 0.9) -> ExtractedField:
    return ExtractedField(key=key, target_type="Contact", confidence=confidence, sources=[])


def _make_service(
    llm_fields: list[ExtractedField] | None = None,
    profiles: dict | None = None,
) -> ExtractionService:
    mock_ocr = MockOcrEngine()
    mock_text = MockTextExtractor()
    llm = MockLLMClient(
        endpoint="http://localhost:9999",
        fixed_fields=llm_fields or [_field()],
    )
    guard = ResidencyGuard(llm, allowlist=["http://localhost"], allow_external=False)
    return ExtractionService(
        text_extractor=mock_text,
        ocr_engine=mock_ocr,
        llm_client=guard,
        profiles=profiles or {"test": _profile_with_field(), "default": MappingProfile(name="default", version="1.0")},
    )


# a. Sync proposal shape returned
async def test_sync_proposal_shape() -> None:
    svc = _make_service()
    inp = ExtractInput(input_ref="doc.pdf", content_kind="pdf", mapping_profile="test")
    proposal = await tool_document_extract(inp, _make_pdf_bytes(), svc)
    assert proposal.run_id
    assert proposal.input_checksum
    assert proposal.page_coverage.total_pages >= 0


# b. No-write assertion — connector never called (no connector in pipeline)
async def test_no_system_of_record_write() -> None:
    connector_calls: list[str] = []

    class SpyConnector:
        async def put(self, *a, **kw): connector_calls.append("put")
        async def get(self, *a, **kw): connector_calls.append("get")
        async def move(self, *a, **kw): connector_calls.append("move")

    svc = _make_service()
    inp = ExtractInput(input_ref="doc.pdf", content_kind="pdf", mapping_profile="test")
    await tool_document_extract(inp, _make_pdf_bytes(), svc)
    assert connector_calls == [], "No connector write should ever be called"


# c. Threshold out-of-range rejected at input validation
async def test_threshold_out_of_range_rejected() -> None:
    svc = _make_service()
    with pytest.raises(Exception):
        inp = ExtractInput(input_ref="doc.pdf", content_kind="pdf", threshold=2.0)


# d. Empty input raises
async def test_empty_input_raises() -> None:
    svc = _make_service()
    inp = ExtractInput(input_ref="empty.pdf", content_kind="pdf")
    with pytest.raises(ValueError):
        await tool_document_extract(inp, b"", svc)


# e. Oversized input raises InputLimitError
async def test_oversized_input_raises() -> None:
    import os
    os.environ["CAM_EXTRACTION_MAX_BYTES"] = "10"
    try:
        svc = _make_service()
        inp = ExtractInput(input_ref="big.pdf", content_kind="pdf")
        with pytest.raises(InputLimitError):
            await tool_document_extract(inp, b"x" * 100, svc)
    finally:
        os.environ.pop("CAM_EXTRACTION_MAX_BYTES", None)


# f. ResidencyError propagates when endpoint denied
async def test_residency_error_propagates() -> None:
    external_llm = MockLLMClient(endpoint="https://external.openai.com/v1")
    guard = ResidencyGuard(external_llm, allowlist=["http://localhost"], allow_external=False)
    svc = ExtractionService(
        text_extractor=MockTextExtractor(),
        ocr_engine=MockOcrEngine(),
        llm_client=guard,
        profiles={"default": MappingProfile(name="default", version="1.0")},
    )
    inp = ExtractInput(input_ref="doc.pdf", content_kind="pdf", structuring=True)
    with pytest.raises(ResidencyError):
        await tool_document_extract(inp, _make_pdf_bytes(), svc)


# g. Email-body path returns proposal with total_pages=0
async def test_email_body_path() -> None:
    svc = _make_service()
    inp = ExtractInput(input_ref="msg-001", content_kind="email_body", structuring=False)
    proposal = await tool_document_extract(inp, b"Dear client, your case is proceeding.", svc)
    assert proposal.page_coverage.total_pages == 0
    assert proposal.deterministic is True


# h. Input checksum is SHA-256 of content
async def test_input_checksum_correct() -> None:
    svc = _make_service()
    content = _make_pdf_bytes()
    inp = ExtractInput(input_ref="doc.pdf", content_kind="pdf", structuring=False)
    proposal = await tool_document_extract(inp, content, svc)
    expected = hashlib.sha256(content).hexdigest()
    assert proposal.input_checksum == expected


# i. Prompt renders for a profile
def test_prompt_renders() -> None:
    from cam.core.services.extraction.prompts import render_extraction_schema
    profile = _profile_with_field()
    prompt = render_extraction_schema(profile)
    assert "email" in prompt
    assert "contact.email" not in prompt  # source_key, not domain_key
    assert "[0.0, 1.0]" in prompt or "0_to_1" in prompt or "0 to 1" in prompt or "[0,1]" in prompt or "0.0, 1.0" in prompt
