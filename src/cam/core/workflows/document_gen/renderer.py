"""Render pipeline — DOCX/HTML + PDF convert + checksum — spec G3.

Unresolved required variables → visible gap placeholder, never empty (FR-9).
Renderer is deterministic: identical inputs → identical bytes.
PDF conversion is a configurable stub (WeasyPrint/LibreOffice not always available).
"""

from __future__ import annotations

import hashlib
import io
import os as _os
import re
import shutil as _shutil
import subprocess as _subprocess
import tempfile as _tempfile
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


# ---------------------------------------------------------------------------
# PDF conversion — configurable engine (LibreOffice headless | WeasyPrint)
# ---------------------------------------------------------------------------

# Engine selection: env var > auto-detect
#   CAM_PDF_ENGINE=auto (default) — try LibreOffice first, then WeasyPrint
#   CAM_PDF_ENGINE=libreoffice    — LibreOffice headless only
#   CAM_PDF_ENGINE=weasyprint     — WeasyPrint only (HTML→PDF; DOCX via HTML intermediary)
#   CAM_PDF_ENGINE=none           — disable PDF conversion entirely


def _detect_pdf_engine() -> str | None:
    """Return the best available PDF engine, or None."""
    preferred = _os.environ.get("CAM_PDF_ENGINE", "auto")
    if preferred == "none":
        return None
    if preferred == "libreoffice":
        return "libreoffice" if _find_libreoffice() else None
    if preferred == "weasyprint":
        return "weasyprint" if _has_weasyprint() else None
    # auto: try LibreOffice first (best DOCX fidelity), then WeasyPrint
    if _find_libreoffice():
        return "libreoffice"
    if _has_weasyprint():
        return "weasyprint"
    return None


def _find_libreoffice() -> str | None:
    """Return the LibreOffice binary path, or None."""
    for name in ("libreoffice", "soffice"):
        path = _shutil.which(name)
        if path:
            return path
    # macOS app bundle path
    mac_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if _os.path.exists(mac_path):
        return mac_path
    return None


def _has_weasyprint() -> bool:
    """Check if WeasyPrint is importable."""
    try:
        import weasyprint  # noqa: F401, PLC0415
        return True
    except ImportError:
        return False


def _libreoffice_to_pdf(docx_bytes: bytes) -> bytes:
    """Convert DOCX to PDF using LibreOffice headless."""
    binary = _find_libreoffice()
    if binary is None:
        raise ConvertError("LibreOffice binary not found on PATH.")

    with _tempfile.TemporaryDirectory() as tmpdir:
        docx_path = _os.path.join(tmpdir, "input.docx")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)

        result = _subprocess.run(
            [
                binary,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", tmpdir,
                docx_path,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise ConvertError(
                f"LibreOffice conversion failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )

        pdf_path = _os.path.join(tmpdir, "input.pdf")
        if not _os.path.exists(pdf_path):
            raise ConvertError("LibreOffice produced no output PDF file.")

        with open(pdf_path, "rb") as f:
            return f.read()


def _weasyprint_to_pdf(content: bytes) -> bytes:
    """Convert HTML bytes to PDF using WeasyPrint."""
    try:
        from weasyprint import HTML  # noqa: PLC0415
    except ImportError:
        raise ConvertError("WeasyPrint is not installed.") from None

    html_string = content.decode("utf-8", errors="replace")
    pdf_bytes = HTML(string=html_string).write_pdf()
    return bytes(pdf_bytes)


def to_pdf(docx_bytes: bytes, mandatory: bool = False) -> bytes | None:
    """Convert DOCX (or HTML) bytes to PDF.

    Engine selection (CAM_PDF_ENGINE env var):
      - ``auto`` (default): LibreOffice headless for DOCX, WeasyPrint for HTML
      - ``libreoffice``:    LibreOffice headless only (best DOCX fidelity)
      - ``weasyprint``:     WeasyPrint only (HTML→PDF; for DOCX, renders via HTML)
      - ``none``:           disable PDF conversion

    Returns None on failure if ``mandatory`` is False; raises ``ConvertError``
    if ``mandatory`` is True and conversion fails.
    """
    engine = _detect_pdf_engine()
    if engine is None:
        if mandatory:
            raise ConvertError(
                "PDF conversion is disabled or no engine is available "
                "(set CAM_PDF_ENGINE=auto and install LibreOffice or WeasyPrint)."
            )
        log.warning("docgen.pdf_convert_unavailable", reason="no_engine")
        return None

    try:
        if engine == "libreoffice":
            # LibreOffice handles DOCX directly
            if docx_bytes[:4] == b"PK\x03\x04":  # ZIP magic = .docx
                return _libreoffice_to_pdf(docx_bytes)
            # For HTML input, write to temp file and convert
            with _tempfile.TemporaryDirectory() as tmpdir:
                html_path = _os.path.join(tmpdir, "input.html")
                with open(html_path, "wb") as f:
                    f.write(docx_bytes)
                result = _subprocess.run(
                    [
                        _find_libreoffice(),  # type: ignore[list-item]
                        "--headless",
                        "--convert-to", "pdf",
                        "--outdir", tmpdir,
                        html_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    raise ConvertError(
                        f"LibreOffice HTML conversion failed: {result.stderr.strip()}"
                    )
                pdf_path = _os.path.join(tmpdir, "input.pdf")
                with open(pdf_path, "rb") as f:
                    return f.read()

        if engine == "weasyprint":
            return _weasyprint_to_pdf(docx_bytes)

        raise ConvertError(f"Unknown PDF engine: {engine!r}")

    except ConvertError:
        if mandatory:
            raise
        log.warning("docgen.pdf_convert_failed", engine=engine)
        return None
    except Exception as exc:
        if mandatory:
            raise ConvertError(f"PDF conversion error: {exc}") from exc
        log.warning("docgen.pdf_convert_error", engine=engine, error=str(exc))
        return None


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
