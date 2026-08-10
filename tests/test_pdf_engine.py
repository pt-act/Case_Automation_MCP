"""PDF rendering engine tests — mocks LibreOffice subprocess and WeasyPrint import."""

from __future__ import annotations

import os
import subprocess
from unittest import mock

import pytest

from cam.core.workflows.document_gen.renderer import (
    ConvertError,
    _detect_pdf_engine,
    _libreoffice_to_pdf,
    _weasyprint_to_pdf,
    to_pdf,
)

_RENDERER = "cam.core.workflows.document_gen.renderer."

# -- Engine detection -------------------------------------------------------


def test_detect_engine_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAM_PDF_ENGINE=none → no engine."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "none")
    assert _detect_pdf_engine() is None


def test_detect_engine_libreoffice(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAM_PDF_ENGINE=libreoffice → libreoffice if binary exists."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "libreoffice")
    with mock.patch(_RENDERER + "_find_libreoffice", return_value="/usr/bin/libreoffice"):
        assert _detect_pdf_engine() == "libreoffice"


def test_detect_engine_libreoffice_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAM_PDF_ENGINE=libreoffice → None if binary missing."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "libreoffice")
    with mock.patch(_RENDERER + "_find_libreoffice", return_value=None):
        assert _detect_pdf_engine() is None


def test_detect_engine_weasyprint(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAM_PDF_ENGINE=weasyprint → weasyprint if importable."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "weasyprint")
    with mock.patch(_RENDERER + "_has_weasyprint", return_value=True):
        assert _detect_pdf_engine() == "weasyprint"


def test_detect_engine_auto_prefers_libreoffice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto mode prefers LibreOffice over WeasyPrint."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "auto")
    with (
        mock.patch(_RENDERER + "_find_libreoffice", return_value="/usr/bin/libreoffice"),
        mock.patch(_RENDERER + "_has_weasyprint", return_value=True),
    ):
        assert _detect_pdf_engine() == "libreoffice"


def test_detect_engine_auto_falls_back_to_weasyprint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto mode falls back to WeasyPrint when LibreOffice absent."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "auto")
    with (
        mock.patch(_RENDERER + "_find_libreoffice", return_value=None),
        mock.patch(_RENDERER + "_has_weasyprint", return_value=True),
    ):
        assert _detect_pdf_engine() == "weasyprint"


def test_detect_engine_auto_none_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto mode → None when no engine available."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "auto")
    with (
        mock.patch(_RENDERER + "_find_libreoffice", return_value=None),
        mock.patch(_RENDERER + "_has_weasyprint", return_value=False),
    ):
        assert _detect_pdf_engine() is None


# -- to_pdf with no engine --------------------------------------------------


def test_to_pdf_no_engine_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No engine available → returns None (non-mandatory)."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "none")
    assert to_pdf(b"fake docx") is None


def test_to_pdf_no_engine_mandatory_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """No engine available + mandatory → raises ConvertError."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "none")
    with pytest.raises(ConvertError, match="no engine"):
        to_pdf(b"fake docx", mandatory=True)


# -- LibreOffice conversion -------------------------------------------------


def test_libreoffice_to_pdf_success() -> None:
    """LibreOffice converts DOCX to PDF via subprocess."""
    fake_pdf = b"%PDF-1.4 fake pdf content"

    def _fake_run(cmd, **kwargs):
        # Simulate LibreOffice writing input.pdf in the output dir
        # The outdir is in cmd args
        outdir_idx = cmd.index("--outdir") + 1
        outdir = cmd[outdir_idx]
        with open(os.path.join(outdir, "input.pdf"), "wb") as f:
            f.write(fake_pdf)
        result = mock.MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        result.stderr = ""
        return result

    with (
        mock.patch(_RENDERER + "_find_libreoffice", return_value="/usr/bin/libreoffice"),
        mock.patch("subprocess.run", side_effect=_fake_run),
    ):
        result = _libreoffice_to_pdf(b"PK\x03\x04 fake docx")
        assert result == fake_pdf


def test_libreoffice_to_pdf_binary_not_found() -> None:
    """LibreOffice binary missing → ConvertError."""
    with mock.patch(_RENDERER + "_find_libreoffice", return_value=None):
        with pytest.raises(ConvertError, match="not found"):
            _libreoffice_to_pdf(b"fake")


def test_libreoffice_to_pdf_subprocess_failure() -> None:
    """LibreOffice subprocess returns non-zero → ConvertError."""
    with (
        mock.patch(_RENDERER + "_find_libreoffice", return_value="/usr/bin/libreoffice"),
        mock.patch("subprocess.run") as run_mock,
    ):
        run_mock.return_value = mock.MagicMock(
            returncode=1, stderr="error", spec=subprocess.CompletedProcess
        )
        with pytest.raises(ConvertError, match="failed"):
            _libreoffice_to_pdf(b"PK\x03\x04 fake")


# -- to_pdf integration with mocked engine ----------------------------------


def test_to_pdf_libreoffice_docx(monkeypatch: pytest.MonkeyPatch) -> None:
    """to_pdf routes DOCX to LibreOffice and returns PDF bytes."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "libreoffice")
    fake_pdf = b"%PDF-1.4 fake"

    with (
        mock.patch(_RENDERER + "_find_libreoffice", return_value="/usr/bin/lo"),
        mock.patch(_RENDERER + "_libreoffice_to_pdf", return_value=fake_pdf),
    ):
        result = to_pdf(b"PK\x03\x04 docx content")
        assert result == fake_pdf


def test_to_pdf_weasyprint_html(monkeypatch: pytest.MonkeyPatch) -> None:
    """to_pdf routes HTML to WeasyPrint."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "weasyprint")
    fake_pdf = b"%PDF-1.4 fake"

    with (
        mock.patch(_RENDERER + "_has_weasyprint", return_value=True),
        mock.patch(_RENDERER + "_weasyprint_to_pdf", return_value=fake_pdf),
    ):
        result = to_pdf(b"<html><body>Hello</body></html>")
        assert result == fake_pdf


def test_to_pdf_convert_error_non_mandatory_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConvertError on non-mandatory → None (graceful fallback)."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "libreoffice")

    with (
        mock.patch(_RENDERER + "_find_libreoffice", return_value="/usr/bin/lo"),
        mock.patch(_RENDERER + "_libreoffice_to_pdf", side_effect=ConvertError("fail")),
    ):
        assert to_pdf(b"PK\x03\x04 docx") is None


def test_to_pdf_convert_error_mandatory_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConvertError on mandatory → raises."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "libreoffice")

    with (
        mock.patch(_RENDERER + "_find_libreoffice", return_value="/usr/bin/lo"),
        mock.patch(_RENDERER + "_libreoffice_to_pdf", side_effect=ConvertError("fail")),
    ):
        with pytest.raises(ConvertError, match="fail"):
            to_pdf(b"PK\x03\x04 docx", mandatory=True)


def test_to_pdf_generic_error_non_mandatory_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-ConvertError on non-mandatory → None (graceful)."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "weasyprint")

    with (
        mock.patch(_RENDERER + "_has_weasyprint", return_value=True),
        mock.patch(_RENDERER + "_weasyprint_to_pdf", side_effect=RuntimeError("boom")),
    ):
        assert to_pdf(b"<html>") is None


def test_to_pdf_generic_error_mandatory_wraps_in_convert_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-ConvertError on mandatory → ConvertError wrapping the original."""
    monkeypatch.setenv("CAM_PDF_ENGINE", "weasyprint")

    with (
        mock.patch(_RENDERER + "_has_weasyprint", return_value=True),
        mock.patch(_RENDERER + "_weasyprint_to_pdf", side_effect=RuntimeError("boom")),
    ):
        with pytest.raises(ConvertError, match="boom"):
            to_pdf(b"<html>", mandatory=True)


# -- WeasyPrint conversion (mocked import) ----------------------------------


def test_weasyprint_to_pdf_success() -> None:
    """WeasyPrint converts HTML to PDF."""
    fake_pdf = b"%PDF-1.4 fake"

    # Mock the weasyprint module
    fake_weasyprint = mock.MagicMock()
    fake_html = mock.MagicMock()
    fake_html.write_pdf.return_value = fake_pdf
    fake_weasyprint.HTML.return_value = fake_html

    with mock.patch.dict("sys.modules", {"weasyprint": fake_weasyprint}):
        result = _weasyprint_to_pdf(b"<html><body>Hello</body></html>")
        assert result == fake_pdf


def test_weasyprint_to_pdf_not_installed() -> None:
    """WeasyPrint not installed → ConvertError."""
    with mock.patch("builtins.__import__", side_effect=ImportError("no weasyprint")):
        with pytest.raises(ConvertError, match="not installed"):
            _weasyprint_to_pdf(b"<html>")
