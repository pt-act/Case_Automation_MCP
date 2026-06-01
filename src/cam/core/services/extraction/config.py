"""Extraction service configuration — spec §4.4.

Loaded from environment via platform-foundation Settings.
All values default to safe/conservative choices.
"""

from __future__ import annotations

import os

DEFAULT_THRESHOLD: float = 0.80           # ASSUMPTION (confirm)
DEFAULT_MAX_PAGES: int = 500              # ASSUMPTION (confirm)
DEFAULT_MAX_BYTES: int = 50 * 1024 * 1024 # 50 MB  ASSUMPTION (confirm)
DEFAULT_OCR_LANGS: str = "eng"


def get_threshold() -> float:
    try:
        return float(os.environ.get("CAM_EXTRACTION_DEFAULT_THRESHOLD", DEFAULT_THRESHOLD))
    except ValueError:
        return DEFAULT_THRESHOLD


def get_max_pages() -> int:
    try:
        return int(os.environ.get("CAM_EXTRACTION_MAX_PAGES", DEFAULT_MAX_PAGES))
    except ValueError:
        return DEFAULT_MAX_PAGES


def get_max_bytes() -> int:
    try:
        return int(os.environ.get("CAM_EXTRACTION_MAX_BYTES", DEFAULT_MAX_BYTES))
    except ValueError:
        return DEFAULT_MAX_BYTES


def allow_external_inference() -> bool:
    val = os.environ.get("CAM_EXTRACTION_ALLOW_EXTERNAL_INFERENCE", "false").lower()
    return val in ("true", "1", "yes")


def get_residency_allowlist() -> list[str]:
    raw = os.environ.get("CAM_EXTRACTION_RESIDENCY_ALLOWLIST", "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_ocr_langs() -> str:
    return os.environ.get("CAM_EXTRACTION_OCR_LANGS", DEFAULT_OCR_LANGS)
