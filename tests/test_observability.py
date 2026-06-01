"""G6 — Observability focused tests (no external sinks required)."""

from __future__ import annotations

import logging

import pytest
import structlog

from cam.obs.observability import bind_run_id, configure_observability, get_run_id, pii_scrub


@pytest.fixture(autouse=True)
def _reset_obs(monkeypatch: pytest.MonkeyPatch) -> None:
    import cam.obs.observability as obs_mod
    monkeypatch.setattr(obs_mod, "_is_configured", False)


def test_configure_observability_idempotent() -> None:
    configure_observability()
    configure_observability()  # second call must be a no-op


def test_run_id_appears_in_context() -> None:
    bind_run_id("run-abc-123")
    assert get_run_id() == "run-abc-123"


def test_pii_scrub_a_number() -> None:
    result = pii_scrub("Applicant A123456789 filed today")
    assert "A123456789" not in result
    assert "[REDACTED]" in result


def test_pii_scrub_ssn() -> None:
    result = pii_scrub("SSN is 123-45-6789")
    assert "123-45-6789" not in result
    assert "[REDACTED]" in result


def test_pii_scrub_email() -> None:
    result = pii_scrub("Contact at jane@lawfirm.com please")
    assert "jane@lawfirm.com" not in result
    assert "[REDACTED]" in result


def test_pii_scrub_clean_text_unchanged() -> None:
    text = "Matter status updated to approved."
    assert pii_scrub(text) == text
