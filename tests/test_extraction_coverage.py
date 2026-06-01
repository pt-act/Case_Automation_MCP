"""G4 — Page coverage accounting focused tests."""

from __future__ import annotations

from cam.core.services.extraction.coverage import build_coverage, build_email_coverage


# a. All-text document
def test_all_text_coverage() -> None:
    cov, warns = build_coverage(3, [1, 2, 3], {}, [])
    assert cov.is_complete()
    assert cov.is_disjoint()
    assert cov.ocr_pages == []
    assert cov.skipped_pages == []
    assert not warns


# b. All-OCR document
def test_all_ocr_coverage() -> None:
    ocr = {1: ("text", 0.9), 2: ("text", 0.8), 3: ("text", 0.7)}
    cov, warns = build_coverage(3, [], ocr, [1, 2, 3])
    assert cov.is_complete()
    assert cov.is_disjoint()
    assert cov.text_pages == []


# c. Mixed document
def test_mixed_coverage() -> None:
    ocr = {3: ("text", 0.7)}
    cov, warns = build_coverage(3, [1, 2], ocr, [3])
    assert cov.is_complete()
    assert cov.is_disjoint()
    assert sorted(cov.text_pages) == [1, 2]
    assert cov.ocr_pages == [3]


# d. One corrupt page → skipped + warning
def test_corrupt_page_skipped() -> None:
    cov, warns = build_coverage(3, [1, 2], {}, [3])  # OCR attempted but returned nothing
    assert 3 in cov.skipped_pages
    assert any("3" in w for w in warns)
    assert cov.is_complete()
    assert cov.is_disjoint()


# e. No page silently dropped
def test_no_silent_drop() -> None:
    ocr = {2: ("text", 0.5)}
    cov, warns = build_coverage(4, [1], ocr, [2])
    # pages 3 and 4 not in any set before build → must end up in skipped
    assert 3 in cov.skipped_pages
    assert 4 in cov.skipped_pages
    assert cov.is_complete()


# f. Email coverage has total_pages=0
def test_email_coverage() -> None:
    cov = build_email_coverage()
    assert cov.total_pages == 0
    assert cov.is_complete()
