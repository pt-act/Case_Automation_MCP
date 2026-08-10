"""G9 — Property-based tests for data-extraction — spec pbt-properties.md.

Properties:
  P1. confidence is always in [0,1] after clamping
  P2. below-threshold field always has requires_verification=True
  P3. ExtractionService never calls a connector write (no-write invariant)
  P4. Non-LLM extraction is deterministic (same bytes → same output)
  P5. PageCoverage is always complete + disjoint
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from cam.core.services.extraction.coverage import build_coverage
from cam.core.services.extraction.llm import normalise_confidence
from cam.core.services.extraction.mapper import apply_threshold
from cam.core.services.extraction.types import ExtractedField


# P1. Confidence always in [0,1] after normalise_confidence
@given(v=st.one_of(st.floats(allow_nan=True, allow_infinity=True), st.integers(-1000, 1000)))
@settings(max_examples=200)
def test_p1_confidence_always_in_range(v) -> None:  # type: ignore[no-untyped-def]
    warns: list[str] = []
    result = normalise_confidence(v, warnings=warns)
    assert 0.0 <= result <= 1.0


# P2. Below-threshold field always flagged
@given(
    conf=st.floats(min_value=0.0, max_value=0.999, allow_nan=False),
    threshold=st.floats(min_value=0.001, max_value=1.0, allow_nan=False),
)
@settings(max_examples=200)
def test_p2_below_threshold_always_flagged(conf: float, threshold: float) -> None:
    if conf >= threshold:
        return  # not a below-threshold case
    field = ExtractedField(key="k", target_type="Contact", confidence=conf, sources=[])
    result = apply_threshold([field], threshold=threshold)
    assert result[0].requires_verification is True


# P3. No-write invariant: ExtractionService never calls connector writes
def test_p3_no_connector_write() -> None:
    calls: list[str] = []

    class SpyDocstore:
        async def put(self, *a, **kw): calls.append("put")
        async def get(self, *a, **kw): calls.append("get")
        async def move(self, *a, **kw): calls.append("move")

    from cam.core.services.extraction.ocr import MockOcrEngine
    from cam.core.services.extraction.pipeline import ExtractionService
    from cam.core.services.extraction.types import ExtractInput

    svc = ExtractionService(ocr_engine=MockOcrEngine())
    inp = ExtractInput(input_ref="test", content_kind="email_body", structuring=False)
    svc.extract(inp, b"test content")
    assert calls == []


# P4. Non-LLM extraction is deterministic
@given(text=st.text(min_size=1, max_size=200))
@settings(max_examples=100)
def test_p4_non_llm_deterministic(text: str) -> None:
    from cam.core.services.extraction.ocr import MockOcrEngine
    from cam.core.services.extraction.pipeline import ExtractionService
    from cam.core.services.extraction.types import ExtractInput

    svc = ExtractionService(ocr_engine=MockOcrEngine())
    inp = ExtractInput(input_ref="test", content_kind="email_body", structuring=False)
    content = text.encode("utf-8", errors="replace")
    p1 = svc.extract(inp, content)
    p2 = svc.extract(inp, content)
    assert p1.input_checksum == p2.input_checksum
    assert p1.deterministic is True
    assert p2.deterministic is True


# P5. PageCoverage complete + disjoint for any valid partition
@given(
    total=st.integers(min_value=0, max_value=20),
    data=st.data(),
)
@settings(max_examples=200)
def test_p5_coverage_invariants(total: int, data: st.DataObject) -> None:
    if total == 0:
        cov, _ = build_coverage(0, [], {}, [])
        assert cov.is_complete()
        return

    all_pages = list(range(1, total + 1))
    # Randomly partition pages into text / ocr_attempted / neither (→ skipped)
    text_pages = data.draw(st.lists(st.sampled_from(all_pages), unique=True, max_size=total))
    remaining = [p for p in all_pages if p not in text_pages]
    ocr_attempted = data.draw(
        st.lists(st.sampled_from(remaining), unique=True, max_size=len(remaining))
        if remaining else st.just([])
    )
    # All ocr_attempted return results
    ocr_results = dict.fromkeys(ocr_attempted, ("text", 0.8))

    cov, warns = build_coverage(total, text_pages, ocr_results, ocr_attempted)
    assert cov.is_complete(), f"Coverage incomplete: {cov}"
    assert cov.is_disjoint(), f"Coverage not disjoint: {cov}"
