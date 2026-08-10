"""Text extraction stage — pdfplumber / PyMuPDF — spec §5 (happy path step 2).

Extracts the native text layer per page.  Pages with no usable text are
flagged for OCR.  Email bodies bypass paging entirely.  Output is
deterministic for identical bytes (PTD §10).
"""

from __future__ import annotations

import io
from typing import Protocol, runtime_checkable

from cam.core.services.extraction.errors import UnsupportedInputError

_MIN_TEXT_CHARS = 10  # a page with fewer chars is considered "no usable text"


@runtime_checkable
class TextExtractor(Protocol):
    """Port for the text-layer extraction stage."""

    def extract(
        self, content: bytes, kind: str
    ) -> tuple[dict[int, str], list[int], list[int]]:
        """Extract text from content.

        Returns:
            (page_texts, text_pages, ocr_needed_pages)
            page_texts: {1-based page → text}
            text_pages: pages with usable text
            ocr_needed_pages: pages that need OCR
        """
        ...


class PdfTextExtractor:
    """pdfplumber-based text extractor with PyMuPDF fallback.

    Both libraries are tried in order; the first that succeeds wins.
    Email bodies bypass paging and are returned as a single pseudo-page.
    """

    def extract(
        self, content: bytes, kind: str
    ) -> tuple[dict[int, str], list[int], list[int]]:
        if kind == "email_body":
            return self._extract_email_body(content)
        if kind in ("pdf", "image"):
            return self._extract_pdf(content)
        raise UnsupportedInputError(f"Content kind {kind!r} is not supported.")

    def _extract_email_body(
        self, content: bytes
    ) -> tuple[dict[int, str], list[int], list[int]]:
        content.decode("utf-8", errors="replace")
        return {}, [], []  # total_pages=0 for email; handled by coverage builder

    def _extract_pdf(
        self, content: bytes
    ) -> tuple[dict[int, str], list[int], list[int]]:
        # Try pdfplumber first
        try:
            return self._pdfplumber(content)
        except Exception:
            pass
        # Fallback: PyMuPDF
        try:
            return self._pymupdf(content)
        except Exception as exc:
            raise UnsupportedInputError(f"Could not parse PDF: {exc}") from exc

    def _pdfplumber(
        self, content: bytes
    ) -> tuple[dict[int, str], list[int], list[int]]:
        import pdfplumber

        page_texts: dict[int, str] = {}
        text_pages: list[int] = []
        ocr_pages: list[int] = []

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                page_texts[idx] = text
                if len(text.strip()) >= _MIN_TEXT_CHARS:
                    text_pages.append(idx)
                else:
                    ocr_pages.append(idx)

        return page_texts, text_pages, ocr_pages

    def _pymupdf(
        self, content: bytes
    ) -> tuple[dict[int, str], list[int], list[int]]:
        import fitz  # PyMuPDF

        page_texts: dict[int, str] = {}
        text_pages: list[int] = []
        ocr_pages: list[int] = []

        doc = fitz.open(stream=content, filetype="pdf")
        for idx in range(1, len(doc) + 1):
            page = doc[idx - 1]
            text = page.get_text() or ""
            page_texts[idx] = text
            if len(text.strip()) >= _MIN_TEXT_CHARS:
                text_pages.append(idx)
            else:
                ocr_pages.append(idx)
        doc.close()

        return page_texts, text_pages, ocr_pages
