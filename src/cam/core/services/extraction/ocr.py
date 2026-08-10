"""OCR fallback stage — Tesseract via pytesseract — spec §5 (happy path step 3).

Only pages that lack a usable text layer are OCR'd.
Tesseract is optional: if not installed, the engine degrades gracefully
(pages go to skipped_pages with a warning).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class OcrEngine(Protocol):
    """Port for the OCR stage."""

    def ocr(
        self, content: bytes, pages: list[int]
    ) -> dict[int, tuple[str, float]]:
        """Run OCR on specified pages.

        Returns:
            {1-based page → (text, confidence ∈ [0,1])}
        """
        ...


class TesseractOcrEngine:
    """pytesseract-backed OCR engine with graceful fallback."""

    def __init__(self, langs: str = "eng") -> None:
        self._langs = langs
        self._available = self._check_available()

    def _check_available(self) -> bool:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def ocr(self, content: bytes, pages: list[int]) -> dict[int, tuple[str, float]]:
        if not self._available or not pages:
            return dict.fromkeys(pages, ("", 0.0))
        return self._run(content, pages)

    def _run(self, content: bytes, pages: list[int]) -> dict[int, tuple[str, float]]:
        results: dict[int, tuple[str, float]] = {}
        try:
            import fitz
            import pytesseract
            from PIL import Image

            doc = fitz.open(stream=content, filetype="pdf")
            for page_num in pages:
                try:
                    page = doc[page_num - 1]
                    mat = fitz.Matrix(2, 2)  # 2x zoom for better OCR
                    pix = page.get_pixmap(matrix=mat)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    data = pytesseract.image_to_data(
                        img,
                        lang=self._langs,
                        output_type=pytesseract.Output.DICT,
                    )
                    text = pytesseract.image_to_string(img, lang=self._langs)
                    confs = [float(c) for c in data["conf"] if str(c) != "-1"]
                    confidence = (sum(confs) / len(confs) / 100.0) if confs else 0.0
                    results[page_num] = (text, max(0.0, min(1.0, confidence)))
                except Exception:
                    results[page_num] = ("", 0.0)
            doc.close()
        except Exception:
            results = dict.fromkeys(pages, ("", 0.0))
        return results


class MockOcrEngine:
    """Deterministic mock for unit tests — returns configurable results."""

    def __init__(
        self, default_text: str = "OCR text", default_confidence: float = 0.85
    ) -> None:
        self._default_text = default_text
        self._default_confidence = default_confidence
        self._page_overrides: dict[int, tuple[str, float]] = {}
        self.called_pages: list[int] = []

    def set_page(self, page: int, text: str, confidence: float) -> None:
        self._page_overrides[page] = (text, confidence)

    def ocr(self, content: bytes, pages: list[int]) -> dict[int, tuple[str, float]]:
        self.called_pages.extend(pages)
        return {
            p: self._page_overrides.get(p, (self._default_text, self._default_confidence))
            for p in pages
        }
