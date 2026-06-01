"""G3/G4/G5 — All seven checks focused tests + G9 PBT invariants."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cam.core.domain.models import Communication, Contact, Deadline, Document, Matter
from cam.core.services.qc.checks import (
    AttachmentIntegrityCheck,
    CompletenessCheck,
    ExtractionConfidenceCheck,
    ConsistencyCheck,
    DeadlineSanityCheck,
    PrivilegeCheck,
    RecipientIntegrityCheck,
)
from cam.core.services.qc.packet import (
    AttachmentRef,
    PacketKind,
    QCExtractedField,
    TemplateBinding,
    VerificationPacket,
)
from cam.core.services.qc.types import Aggregate, CheckResult, QCConfig, Verdict

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _contact(id: str = "c1", name: str = "Ana Garcia") -> Contact:
    return Contact(id=id, source="crm", name=name, external_ids={})


def _matter(client: Contact | None = None) -> Matter:
    c = client or _contact()
    return Matter(
        id="m1", source="case", reference="REF-001", title="T",
        status="open", client=c, opened_at=NOW, external_ids={},
    )


def _doc(id: str = "d1", privileged: bool = False, checksum: str = "abc", version: int = 1) -> Document:
    return Document(
        id=id, matter_id="m1", name=f"{id}.pdf", mime_type="application/pdf",
        uri=f"s3://{id}", version=version, privileged=privileged,
        checksum=checksum, created_at=NOW,
    )


def _packet(**kwargs) -> VerificationPacket:
    defaults = dict(
        packet_id="p1", kind=PacketKind.GENERIC, matter=_matter(), now=NOW,
    )
    return VerificationPacket(**(defaults | kwargs))


def _cfg(**kwargs) -> QCConfig:
    return QCConfig(**kwargs)


# ────────────────────────────────────────────────
# G3.1 — completeness
# ────────────────────────────────────────────────

def test_completeness_pass() -> None:
    binding = TemplateBinding(
        template_name="welcome",
        required_vars=["name", "date"],
        resolved_vars={"name": "Ana", "date": "2026-06-01"},
    )
    p = _packet(template_bindings=[binding])
    r = CompletenessCheck().run(p, _cfg())
    assert r.verdict == Verdict.PASS


def test_completeness_fail_missing_var() -> None:
    binding = TemplateBinding(
        template_name="welcome",
        required_vars=["name", "date"],
        resolved_vars={"name": "Ana"},  # date missing
    )
    p = _packet(template_bindings=[binding])
    r = CompletenessCheck().run(p, _cfg())
    assert r.verdict == Verdict.FAIL


def test_completeness_fail_empty_var() -> None:
    binding = TemplateBinding(
        template_name="welcome",
        required_vars=["name"],
        resolved_vars={"name": "  "},  # empty after strip
    )
    p = _packet(template_bindings=[binding])
    r = CompletenessCheck().run(p, _cfg())
    assert r.verdict == Verdict.FAIL


def test_completeness_skipped_no_bindings() -> None:
    p = _packet()
    r = CompletenessCheck().run(p, _cfg())
    assert r.verdict == Verdict.SKIPPED


# ────────────────────────────────────────────────
# G3.2 — consistency
# ────────────────────────────────────────────────

def test_consistency_pass() -> None:
    client = _contact()
    matter = _matter(client)
    doc = _doc()
    p = _packet(matter=matter, documents=[doc])
    r = ConsistencyCheck().run(p, _cfg())
    assert r.verdict == Verdict.PASS


def test_consistency_fail_document_wrong_matter() -> None:
    matter = _matter()
    bad_doc = Document(
        id="d_bad", matter_id="WRONG_MATTER", name="bad.pdf",
        mime_type="application/pdf", uri="s3://bad", version=1,
        privileged=False, checksum="x", created_at=NOW,
    )
    p = _packet(matter=matter, documents=[bad_doc])
    r = ConsistencyCheck().run(p, _cfg())
    assert r.verdict == Verdict.FAIL


def test_consistency_warn_near_match_name() -> None:
    client = _contact(name="Ana Garcia")
    matter = _matter(client)
    # Same contact but with slightly different name spacing in a comm participant
    near_match = _contact(id="c1", name="Ana  Garcia")  # double space
    comm = Communication(
        id="comm1", matter_id="m1", direction="out", channel="email",
        body="Hello", status="draft", participants=[near_match],
    )
    p = _packet(matter=matter, communications=[comm])
    r = ConsistencyCheck().run(p, _cfg())
    assert r.verdict == Verdict.WARN


# ────────────────────────────────────────────────
# G3.3 — attachment_integrity
# ────────────────────────────────────────────────

def test_attachment_pass() -> None:
    doc = _doc(checksum="abc123", version=2)
    ref = AttachmentRef(referenced_name="d1.pdf", expected_checksum="abc123", expected_version=2)
    p = _packet(documents=[doc], attachment_refs=[ref])
    r = AttachmentIntegrityCheck().run(p, _cfg())
    assert r.verdict == Verdict.PASS


def test_attachment_fail_missing() -> None:
    ref = AttachmentRef(referenced_name="missing.pdf")
    p = _packet(attachment_refs=[ref])
    r = AttachmentIntegrityCheck().run(p, _cfg())
    assert r.verdict == Verdict.FAIL


def test_attachment_fail_checksum_mismatch() -> None:
    doc = _doc(checksum="real_checksum")
    ref = AttachmentRef(referenced_name="d1.pdf", expected_checksum="wrong_checksum")
    p = _packet(documents=[doc], attachment_refs=[ref])
    r = AttachmentIntegrityCheck().run(p, _cfg())
    assert r.verdict == Verdict.FAIL


def test_attachment_warn_newer_version() -> None:
    doc = _doc(checksum="abc", version=3)
    ref = AttachmentRef(referenced_name="d1.pdf", expected_version=2)
    p = _packet(documents=[doc], attachment_refs=[ref])
    r = AttachmentIntegrityCheck().run(p, _cfg())
    assert r.verdict == Verdict.WARN


# ────────────────────────────────────────────────
# G4.1 — recipient_integrity
# ────────────────────────────────────────────────

def test_recipient_pass() -> None:
    client = _contact()
    matter = _matter(client)
    p = _packet(
        kind=PacketKind.EMAIL_SEND,
        matter=matter,
        intended_recipients=[client],
    )
    r = RecipientIntegrityCheck().run(p, _cfg())
    assert r.verdict == Verdict.PASS


def test_recipient_fail_unknown() -> None:
    client = _contact()
    matter = _matter(client)
    stranger = _contact(id="stranger", name="Unknown Person")
    p = _packet(kind=PacketKind.EMAIL_SEND, matter=matter, intended_recipients=[stranger])
    r = RecipientIntegrityCheck().run(p, _cfg())
    assert r.verdict == Verdict.FAIL


def test_recipient_fail_empty() -> None:
    p = _packet(kind=PacketKind.EMAIL_SEND)
    r = RecipientIntegrityCheck().run(p, _cfg())
    assert r.verdict == Verdict.FAIL


# ────────────────────────────────────────────────
# G4.2 — privilege  (the critical invariant)
# ────────────────────────────────────────────────

def test_privilege_fail_external_with_privileged_doc() -> None:
    doc = _doc(privileged=True)
    p = _packet(documents=[doc], external_bound=True)
    r = PrivilegeCheck().run(p, _cfg())
    assert r.verdict == Verdict.FAIL


def test_privilege_pass_internal_privileged_doc() -> None:
    doc = _doc(privileged=True)
    p = _packet(documents=[doc], external_bound=False)
    r = PrivilegeCheck().run(p, _cfg())
    assert r.verdict == Verdict.PASS


def test_privilege_pass_no_privileged_docs() -> None:
    doc = _doc(privileged=False)
    p = _packet(documents=[doc], external_bound=True)
    r = PrivilegeCheck().run(p, _cfg())
    assert r.verdict == Verdict.PASS


def test_privilege_fail_ambiguous_external_derived() -> None:
    """Caller says internal but recipient is not a matter participant → external."""
    client = _contact()
    matter = _matter(client)
    stranger = _contact(id="outsider", name="External Party")
    doc = _doc(privileged=True)
    p = _packet(
        matter=matter,
        documents=[doc],
        external_bound=False,  # caller claims internal
        intended_recipients=[stranger],  # but stranger is not a participant
    )
    r = PrivilegeCheck().run(p, _cfg())
    assert r.verdict == Verdict.FAIL  # fail-closed: re-derived as external


def test_privilege_never_emits_warn() -> None:
    """The privilege check severity policy never allows warn."""
    assert Verdict.WARN not in PrivilegeCheck().severity_policy.allowed


# ────────────────────────────────────────────────
# G5.1 — deadline_sanity
# ────────────────────────────────────────────────

def test_deadline_pass() -> None:
    dl = Deadline(id="dl1", matter_id="m1", name="RFE", due_at=NOW + timedelta(days=30))
    p = _packet(deadlines=[dl])
    r = DeadlineSanityCheck().run(p, _cfg())
    assert r.verdict == Verdict.PASS


def test_deadline_fail_past_due() -> None:
    dl = Deadline(id="dl1", matter_id="m1", name="RFE", due_at=NOW - timedelta(days=1))
    p = _packet(deadlines=[dl])
    r = DeadlineSanityCheck().run(p, _cfg())
    assert r.verdict == Verdict.FAIL


def test_deadline_fail_beyond_horizon() -> None:
    dl = Deadline(id="dl1", matter_id="m1", name="RFE", due_at=NOW + timedelta(days=9999))
    p = _packet(deadlines=[dl])
    r = DeadlineSanityCheck().run(p, _cfg(deadline_horizon_days=365))
    assert r.verdict == Verdict.FAIL


def test_deadline_warn_tight_window() -> None:
    dl = Deadline(id="dl1", matter_id="m1", name="RFE", due_at=NOW + timedelta(days=3))
    p = _packet(deadlines=[dl])
    r = DeadlineSanityCheck().run(p, _cfg(deadline_tight_window_days=7))
    assert r.verdict == Verdict.WARN


def test_deadline_uses_packet_now_not_system_clock() -> None:
    """Verify determinism: same packet → same result regardless of wall clock."""
    future_now = NOW + timedelta(days=365)
    dl = Deadline(id="dl1", matter_id="m1", name="RFE", due_at=NOW + timedelta(days=30))
    p1 = _packet(deadlines=[dl], now=NOW)
    p2 = _packet(deadlines=[dl], now=future_now)
    r1 = DeadlineSanityCheck().run(p1, _cfg())
    r2 = DeadlineSanityCheck().run(p2, _cfg())
    # With future_now, the deadline is in the past → different verdicts → determinism confirmed
    assert r1.verdict != r2.verdict


def test_deadline_skipped_no_deadlines() -> None:
    p = _packet()
    r = DeadlineSanityCheck().run(p, _cfg())
    assert r.verdict == Verdict.SKIPPED


# ────────────────────────────────────────────────
# G5.2 — extraction_confidence
# ────────────────────────────────────────────────

def test_confidence_pass() -> None:
    field = QCExtractedField(id="f1", name="email", confidence=0.95)
    p = _packet(extracted_fields=[field])
    r = ExtractionConfidenceCheck().run(p, _cfg(extraction_warn_threshold=0.80, extraction_fail_floor=0.50))
    assert r.verdict == Verdict.PASS


def test_confidence_warn_between_floor_and_threshold() -> None:
    field = QCExtractedField(id="f1", name="email", confidence=0.65)
    p = _packet(extracted_fields=[field])
    r = ExtractionConfidenceCheck().run(p, _cfg(extraction_warn_threshold=0.80, extraction_fail_floor=0.50))
    assert r.verdict == Verdict.WARN


def test_confidence_fail_below_floor() -> None:
    field = QCExtractedField(id="f1", name="email", confidence=0.30)
    p = _packet(extracted_fields=[field])
    r = ExtractionConfidenceCheck().run(p, _cfg(extraction_warn_threshold=0.80, extraction_fail_floor=0.50))
    assert r.verdict == Verdict.FAIL


def test_confidence_skipped_no_fields() -> None:
    p = _packet()
    r = ExtractionConfidenceCheck().run(p, _cfg())
    assert r.verdict == Verdict.SKIPPED


# ────────────────────────────────────────────────
# G9 PBT — any-fail-blocks + privilege invariant
# ────────────────────────────────────────────────

@given(has_privileged=st.booleans(), external_bound=st.booleans())
@settings(max_examples=100)
def test_pbt_privilege_never_passes_privileged_external(
    has_privileged: bool, external_bound: bool
) -> None:
    doc = _doc(privileged=has_privileged)
    p = _packet(documents=[doc], external_bound=external_bound)
    r = PrivilegeCheck().run(p, _cfg())
    if has_privileged and external_bound:
        assert r.verdict == Verdict.FAIL, "Privileged doc + external → must fail"
    assert r.verdict != Verdict.WARN, "Privilege check must never emit warn"


@given(
    verdicts=st.lists(
        st.sampled_from([Verdict.PASS, Verdict.WARN, Verdict.FAIL, Verdict.SKIPPED]),
        min_size=1, max_size=10,
    )
)
@settings(max_examples=200)
def test_pbt_any_fail_blocks(verdicts) -> None:
    from cam.core.services.qc.registry import _aggregate
    results = [
        CheckResult(check_id=f"c{i}", check_version="1.0", verdict=v)
        for i, v in enumerate(verdicts)
    ]
    agg = _aggregate(results)
    if Verdict.FAIL in verdicts:
        assert agg == Aggregate.BLOCK
    elif Verdict.WARN in verdicts:
        assert agg == Aggregate.PASS_WITH_WARNINGS
    else:
        assert agg == Aggregate.PASS
