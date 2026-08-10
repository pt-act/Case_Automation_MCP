"""G1 — Types, validators, config focused tests."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from cam.core.services.extraction.types import (
    ExtractedField,
    ExtractInput,
    ExtractionSource,
    PageCoverage,
)


def _field(confidence: float, key: str = "contact.email") -> ExtractedField:
    return ExtractedField(
        key=key,
        target_type="Contact",
        confidence=confidence,
        sources=[],
    )


# a. Confidence bounds validator rejects out-of-range / NaN
def test_confidence_clamped_above_one() -> None:
    f = _field(1.3)
    assert f.confidence == 1.0


def test_confidence_clamped_below_zero() -> None:
    f = _field(-0.2)
    assert f.confidence == 0.0


def test_confidence_nan_becomes_zero() -> None:
    f = _field(math.nan)
    assert f.confidence == 0.0


def test_confidence_valid_passes() -> None:
    f = _field(0.75)
    assert f.confidence == 0.75


# b. ExtractInput threshold validation [0,1]
def test_extract_input_threshold_out_of_range() -> None:
    with pytest.raises(ValidationError):
        ExtractInput(input_ref="doc1", content_kind="pdf", threshold=1.5)


def test_extract_input_valid() -> None:
    inp = ExtractInput(input_ref="doc1", content_kind="pdf", threshold=0.9)
    assert inp.threshold == 0.9


# c. ExtractionProposal serialises/deserialises
def test_proposal_round_trip() -> None:
    from cam.core.services.extraction.types import ExtractionProposal

    ExtractionSource(page=1, method="text")
    field = _field(0.8)
    cov = PageCoverage(total_pages=1, text_pages=[1])
    proposal = ExtractionProposal(
        run_id="r1",
        input_ref="doc.pdf",
        input_checksum="abc",
        fields=[field],
        page_coverage=cov,
        structuring_status="ok",
        deterministic=True,
        warnings=[],
    )
    dumped = proposal.model_dump_json()
    reloaded = ExtractionProposal.model_validate_json(dumped)
    assert reloaded.run_id == "r1"
    assert reloaded.fields[0].confidence == 0.8


# d. PageCoverage disjoint + complete helpers
def test_page_coverage_is_complete() -> None:
    cov = PageCoverage(total_pages=3, text_pages=[1, 2], ocr_pages=[3])
    assert cov.is_complete()
    assert cov.is_disjoint()


def test_page_coverage_not_complete() -> None:
    cov = PageCoverage(total_pages=3, text_pages=[1])
    assert not cov.is_complete()


def test_page_coverage_not_disjoint() -> None:
    cov = PageCoverage(total_pages=2, text_pages=[1, 2], ocr_pages=[2])
    assert not cov.is_disjoint()
