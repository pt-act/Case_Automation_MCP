"""Tests for the domain-packs scaffolding (Phase A).

Covers the contract, registry/selection, fail-closed validation, the two
engine-owned safety guarantees (restriction tighten-only, PII floor), and the
`Document.restricted`/`privileged` alias. The full immigration-parity and
cross-pack end-to-end suites land with the consumer rewiring (task group G6).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from cam.core.domain.models import Document
from cam.packs import (
    ENGINE_PII_BASELINE,
    PackSelectionError,
    PackValidationError,
    activate_configured_pack,
    active_pii_patterns,
    get_active_pack,
    register_builtin_packs,
    reset_registry,
    run_pack_conformance,
    select_pack,
)
from cam.packs.base import RestrictionPolicy, Terminology
from cam.packs.consulting import CONSULTING_PACK
from cam.packs.immigration import IMMIGRATION_PACK


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def _scrub(text: str, patterns) -> str:
    for p in patterns:
        text = p.sub("X", text)
    return text


def _make_doc(privileged: bool = True) -> Document:
    return Document(
        id="doc1", matter_id="m1", name="f.pdf", mime_type="application/pdf",
        uri="s3://b/f.pdf", version=1, privileged=privileged,
        checksum="0" * 64, created_at=datetime.now(UTC),
    )


# --- conformance -----------------------------------------------------------


def test_reference_packs_pass_conformance():
    assert run_pack_conformance(IMMIGRATION_PACK) is True
    assert run_pack_conformance(CONSULTING_PACK) is True


# --- registry & selection --------------------------------------------------


def test_selection_activates_pack():
    register_builtin_packs()
    select_pack("immigration")
    assert get_active_pack().name == "immigration"


def test_unknown_pack_refuses_to_serve():
    register_builtin_packs()
    with pytest.raises(PackSelectionError):
        select_pack("does-not-exist")


def test_no_active_pack_before_selection():
    with pytest.raises(PackSelectionError):
        get_active_pack()


def test_no_implicit_default():
    with pytest.raises(PackSelectionError):
        activate_configured_pack(None)
    with pytest.raises(PackSelectionError):
        activate_configured_pack("")


def test_activate_configured_pack_selects_by_name():
    activate_configured_pack("consulting")
    assert get_active_pack().name == "consulting"


# --- safety guarantee 1: PII floor (additive, non-removable) ---------------


def test_pii_floor_preserved_and_immigration_adds():
    patterns = active_pii_patterns(IMMIGRATION_PACK)
    for p in ENGINE_PII_BASELINE:
        assert p in patterns                       # floor preserved
    # baseline universals
    assert "X" in _scrub("a@b.com", patterns)
    assert _scrub("123-45-6789", patterns) == "X"
    # immigration addition: A-number redacted
    assert "A012345678" not in _scrub("A012345678", patterns)


def test_consulting_inherits_floor_adds_nothing():
    patterns = active_pii_patterns(CONSULTING_PACK)
    assert len(patterns) == len(ENGINE_PII_BASELINE)   # no additions
    assert "X" in _scrub("a@b.com", patterns)          # floor still applies
    # consulting does NOT redact an A-number (no immigration pattern)
    assert _scrub("A012345678", patterns) == "A012345678"


# --- safety guarantee 2: restriction tighten-only --------------------------


def test_restriction_default_restricted_true():
    assert IMMIGRATION_PACK.restriction.default_restricted is True
    assert CONSULTING_PACK.restriction.default_restricted is True


def test_weakened_restriction_rejected():
    weak = dataclasses.replace(
        CONSULTING_PACK,
        restriction=RestrictionPolicy(label="Loose", default_restricted=False),
    )
    with pytest.raises(PackValidationError):
        run_pack_conformance(weak)


def test_extra_predicate_can_only_tighten():
    policy = RestrictionPolicy(
        label="Confidential",
        extra_predicates=(lambda ctx: ctx.get("flagged", False),),
    )
    assert policy.extra_block({"flagged": True}) is True
    assert policy.extra_block({"flagged": False}) is False


# --- validation: referential integrity -------------------------------------


def test_missing_template_reference_rejected():
    broken = dataclasses.replace(CONSULTING_PACK, template_ids=frozenset({"default_welcome"}))
    with pytest.raises(PackValidationError):
        run_pack_conformance(broken)   # 'advisory' references consulting_welcome


def test_missing_agent_service_role_rejected():
    roles = {k: v for k, v in CONSULTING_PACK.rbac_roles.items() if k != "agent_service"}
    broken = dataclasses.replace(CONSULTING_PACK, rbac_roles=roles)
    with pytest.raises(PackValidationError):
        run_pack_conformance(broken)


def test_bad_default_case_type_rejected():
    broken = dataclasses.replace(CONSULTING_PACK, default_case_type="nonexistent")
    with pytest.raises(PackValidationError):
        run_pack_conformance(broken)


# --- Document.restricted <-> privileged alias ------------------------------


def test_restricted_aliases_privileged():
    doc = _make_doc(privileged=True)
    assert doc.restricted is True
    doc.restricted = False
    assert doc.privileged is False
    assert _make_doc(privileged=False).restricted is False


# --- terminology -----------------------------------------------------------


def test_terminology_differs_per_pack():
    assert IMMIGRATION_PACK.terminology.restriction_label == "Privileged"
    assert CONSULTING_PACK.terminology.restriction_label == "Client-Confidential"
    assert IMMIGRATION_PACK.terminology.matter == "Matter"
    assert CONSULTING_PACK.terminology.matter == "Engagement"


def test_blank_terminology_slot_rejected():
    with pytest.raises(ValueError):
        Terminology(
            matter="", matter_plural="X", contact="X", contact_plural="X",
            deadline="X", document="X", restriction_label="X", practice_noun="X",
        )


# --- immutability ----------------------------------------------------------


def test_pack_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        IMMIGRATION_PACK.name = "mutated"  # type: ignore[misc]


# --- coupling guard: immigration PII no longer hardcoded in the engine ------


def test_immigration_pii_not_hardcoded_in_observability():
    """G6.1 inversion: the A-number/passport patterns were moved OUT of the
    engine's observability module into the immigration pack. The engine keeps
    only the universal baseline floor."""
    from pathlib import Path

    import cam.obs.observability as obs

    src = Path(obs.__file__).read_text()
    assert r"A\d{8,9}" not in src              # A-number not hardcoded in engine
    assert r"[A-Z]{1,2}\d{6,9}" not in src     # passport not hardcoded in engine
    # ...and the immigration pack supplies them
    assert any(p.search("A123456789") for p in IMMIGRATION_PACK.pii_patterns)


# --- task 6.6: full no-hardcoded-immigration sweep across core ---------------

# Immigration-specific tokens that, per spec §G6.6, must not appear in any core
# module once the consumer rewiring is complete — they belong in `packs/` (or
# docs) only. Generic words are deliberately excluded to avoid false positives.
_IMMIGRATION_TOKENS = (
    "family-based",
    "employment-based",
    "I-130",
    "I-485",
    "N-400",
    "EOIR",
    r"A\d{8,9}",  # A-number validation/PII regex
)


def _core_immigration_offenders() -> dict[str, list[str]]:
    """Return {relative_path: [matched tokens]} for core modules that still
    hard-code an immigration value. `packs/` is the only sanctioned home."""
    from pathlib import Path

    import cam

    src_root = Path(cam.__file__).resolve().parent
    offenders: dict[str, list[str]] = {}
    for py in src_root.rglob("*.py"):
        if "packs" in py.relative_to(src_root).parts:
            continue
        text = py.read_text()
        hits = [tok for tok in _IMMIGRATION_TOKENS if tok in text]
        if hits:
            offenders[str(py.relative_to(src_root))] = hits
    return offenders


def test_no_immigration_value_hardcoded_in_core():
    """G6.6: a core module must not carry an immigration token outside `packs/`.

    The immigration case types, A-number format, and document form ids live in
    the immigration pack; the engine reads them from the active pack. This guard
    fails if any immigration token reappears in core."""
    offenders = _core_immigration_offenders()
    assert offenders == {}, (
        "immigration values still hard-coded in core (move into packs/): "
        f"{offenders}"
    )
