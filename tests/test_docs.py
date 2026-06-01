"""G8 — Doc generator focused tests."""

from __future__ import annotations

import pytest
from pathlib import Path
from pydantic import BaseModel

from cam.docs.generator import generate_model_doc, generate_module_docs


class WellDocumented(BaseModel):
    """A properly documented model for testing."""
    name: str


class MissingDoc(BaseModel):
    name: str


# 1. Generates expected doc for a model
def test_generates_doc_for_model() -> None:
    doc = generate_model_doc(WellDocumented)
    assert "WellDocumented" in doc
    assert "A properly documented model for testing" in doc
    assert "name" in doc


# 2. Missing description flagged
def test_missing_description_raises() -> None:
    with pytest.raises(ValueError, match="MissingDoc"):
        generate_module_docs([MissingDoc])


# 3. Deterministic output
def test_deterministic_output() -> None:
    doc1 = generate_model_doc(WellDocumented)
    doc2 = generate_model_doc(WellDocumented)
    assert doc1 == doc2


# 4. check_drift returns True when in sync
def test_drift_check_in_sync(tmp_path: Path) -> None:
    from cam.docs.generator import check_drift, write_docs
    docs_path = tmp_path / "domain.md"
    write_docs([WellDocumented], docs_path)
    assert check_drift([WellDocumented], docs_path) is True


# 5. check_drift returns False when out of sync
def test_drift_check_detects_drift(tmp_path: Path) -> None:
    from cam.docs.generator import check_drift
    docs_path = tmp_path / "domain.md"
    docs_path.write_text("stale content")
    assert check_drift([WellDocumented], docs_path) is False
