"""Page-coverage accounting — spec §5 steps 3-4, §3 PageCoverage INVARIANT.

Guarantees:
  - Every page 1..total_pages appears in exactly one of: text_pages, ocr_pages, skipped_pages
  - The three sets are pairwise disjoint
  - Corrupt / unprocessable pages go to skipped_pages (surfaced, never silently dropped)
"""

from __future__ import annotations

from cam.core.services.extraction.types import PageCoverage


def build_coverage(
    total_pages: int,
    text_pages: list[int],
    ocr_results: dict[int, tuple[str, float]],  # page → (text, conf)
    attempted_ocr_pages: list[int],
) -> tuple[PageCoverage, list[str]]:
    """Build a PageCoverage ensuring completeness and disjointness.

    Pages in `attempted_ocr_pages` that aren't in `ocr_results` are skipped.
    Warnings are returned for skipped pages.

    Returns:
        (PageCoverage, warnings)
    """
    warnings: list[str] = []
    text_set = set(text_pages)
    ocr_set = set(ocr_results.keys())
    attempted_set = set(attempted_ocr_pages)
    all_pages = set(range(1, total_pages + 1))

    # Pages attempted but not returned by OCR → skipped
    skipped_from_ocr = attempted_set - ocr_set
    # Pages not in text, not in OCR, not in attempted → also skipped
    remaining = all_pages - text_set - ocr_set - skipped_from_ocr
    skipped_set = skipped_from_ocr | remaining

    for p in sorted(skipped_set):
        warnings.append(f"Page {p} could not be processed and was skipped.")

    coverage = PageCoverage(
        total_pages=total_pages,
        text_pages=sorted(text_set),
        ocr_pages=sorted(ocr_set),
        skipped_pages=sorted(skipped_set),
    )
    return coverage, warnings


def build_email_coverage() -> PageCoverage:
    """Coverage for email-body inputs (no paging concept)."""
    return PageCoverage(total_pages=0, text_pages=[], ocr_pages=[], skipped_pages=[])
