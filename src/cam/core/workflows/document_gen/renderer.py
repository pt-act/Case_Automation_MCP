"""Render pipeline — DOCX/HTML + PDF convert + checksum — spec G3.

Unresolved required variables → visible gap placeholder, never empty (FR-9).
Renderer is deterministic: identical inputs → identical bytes.
PDF conversion is a configurable stub (WeasyPrint/LibreOffice not always available).
"""

from __future__ import annotations

import hashlib
import io
import re
from typing import Any

import structlog

from cam.core.workflows.document_gen.types import Gap

log = structlog.get_logger(__name__)

GAP_TOKEN = "[[MISSING: {label}]]"
_JINJA_VAR_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


class RenderError(Exception):
    pass


class ConvertError(Exception):
    pass


def render_docx(
    template_bytes: bytes,
    context: dict[str, Any],
    gaps: list[Gap],
) -> bytes:
    """Render a Jinja2/docxtpl template to DOCX bytes.

    Unresolved required vars (those in `gaps`) are replaced with a visible
    placeholder token.  Optional missing vars are left empty.

    Uses docxtpl for real .docx templates; falls back to plain-text Jinja2
    rendering for HTML and test templates.
    """
    # Build substitution context: fill gaps with placeholder tokens
    gap_vars = {g.variable: GAP_TOKEN.format(label=g.label) for g in gaps}
    render_ctx = {**context, **gap_vars}

    template_text = template_bytes.decode("utf-8", errors="replace")

    # Try docxtpl for .docx binary content; fall back to string substitution
    if template_bytes[:4] == b"PK\x03\x04":  # ZIP magic = .docx
        return _render_docxtpl(template_bytes, render_ctx)

    # Plain-text/HTML template (test path)
    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        val = render_ctx.get(key)
        return str(val) if val is not None else ""

    rendered = _JINJA_VAR_RE.sub(_sub, template_text)
    return rendered.encode("utf-8")


def _render_docxtpl(template_bytes: bytes, context: dict[str, Any]) -> bytes:
    try:
        from docxtpl import DocxTemplate
        tpl = DocxTemplate(io.BytesIO(template_bytes))
        tpl.render(context)
        out = io.BytesIO()
        tpl.save(out)
        return out.getvalue()
    except ImportError:
        raise RenderError("docxtpl is not installed; cannot render .docx templates.") from None
    except Exception as exc:
        raise RenderError(f"DOCX render failed: {exc}") from exc


def to_pdf(docx_bytes: bytes, mandatory: bool = False) -> bytes | None:
    """Convert DOCX bytes to PDF.  Returns None on failure if not mandatory."""
    try:
        # WeasyPrint / LibreOffice headless — stub: not available in test env
        # ASSUMPTION (confirm): preferred PDF engine
        raise ConvertError("PDF conversion not available in this environment.")
    except ConvertError:
        if mandatory:
            raise
        log.warning("docgen.pdf_convert_unavailable")
        return None


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
