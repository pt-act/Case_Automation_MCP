"""Cross-pack end-to-end proof (G6.7 / DP-8).

The same engine, run under the consulting pack, produces correct *different*
domain behaviour — different case types, document classes, confidentiality label,
and PII set — with zero engine/core edits (only `src/cam/packs/` was added).

The autouse conftest activates the immigration pack; each test here re-selects
and applies the consulting pack, then asserts the engine reflects it. Switching
back to immigration restores the original behaviour, proving the inversion is
fully pack-driven.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cam.core.domain.models import Contact, Document, Matter
from cam.core.services.qc.checks.privilege import PrivilegeCheck
from cam.core.services.qc.packet import PacketKind, VerificationPacket
from cam.core.services.qc.types import QCConfig, Verdict
from cam.core.workflows.document_routing.config import get_routing_config
from cam.core.workflows.intake.config import get_intake_config
from cam.packs import active_pii_patterns, apply_pack, get_active_pack, select_pack


@pytest.fixture()
def consulting():
    """Activate + apply the consulting pack for this test (conftest already
    registered the built-in packs and activated immigration)."""
    select_pack("consulting")
    apply_pack()
    return get_active_pack()


def _scrub(text: str) -> str:
    out = text
    for p in active_pii_patterns():
        out = p.sub("X", out)
    return out


def _packet(*, restricted: bool, external: bool) -> VerificationPacket:
    client = Contact(id="c1", source="crm", name="Acme Corp")
    matter = Matter(
        id="m1", source="case", reference="ENG-001", title="Advisory engagement",
        status="open", client=client, opened_at=datetime.now(UTC),
    )
    doc = Document(
        id="d1", matter_id="m1", name="memo.pdf", mime_type="application/pdf",
        uri="s3://b/d1", version=1, privileged=restricted, checksum="0" * 64,
        created_at=datetime.now(UTC),
    )
    recipients = [Contact(id="x9", source="crm", name="Outside Party")] if external else [client]
    return VerificationPacket(
        packet_id="p1", kind=PacketKind.DOCUMENT_ROUTE, matter=matter,
        documents=[doc], intended_recipients=recipients,
        external_bound=external, now=datetime.now(UTC),
        config_snapshot=QCConfig(),
    )


# --- intake: consulting case types, not immigration ------------------------


def test_intake_uses_consulting_case_types(consulting):
    cfg = get_intake_config()
    assert set(cfg.case_type_configs) == {"advisory", "audit"}
    assert cfg.default_case_type == "advisory"
    assert "family-based" not in cfg.case_type_configs


# --- routing: consulting document classes, not immigration -----------------


def test_routing_uses_consulting_classes(consulting):
    cfg = get_routing_config()
    assert "report" in cfg.classes and "working_papers" in cfg.classes
    assert "court_filing" not in cfg.classes          # immigration-only class
    assert cfg.folder_map.folder_for("report") == "docs/report"


# --- confidentiality gate: labelled "Client-Confidential", still fail-closed -


def test_restriction_label_is_consulting(consulting):
    check = PrivilegeCheck()
    # external-bound restricted doc → FAIL, with the consulting label
    fail = check.run(_packet(restricted=True, external=True), QCConfig())
    assert fail.verdict == Verdict.FAIL
    assert "Client-Confidential" in fail.reason
    # internal restricted doc → PASS, consulting label
    ok = check.run(_packet(restricted=True, external=False), QCConfig())
    assert ok.verdict == Verdict.PASS
    assert "Client-Confidential" in ok.reason


def test_restriction_still_fail_closed_under_consulting(consulting):
    # The engine-fixed guarantee holds regardless of pack: a restricted doc never
    # reaches an external recipient.
    result = PrivilegeCheck().run(_packet(restricted=True, external=True), QCConfig())
    assert result.verdict == Verdict.FAIL


# --- PII: consulting inherits the floor, adds nothing ----------------------


def test_pii_floor_under_consulting(consulting):
    assert "X" in _scrub("reach me at jane@firm.com")   # baseline email redacted
    assert _scrub("A123456789") == "A123456789"          # no immigration A-number pattern


# --- switching back to immigration restores original behaviour -------------


def test_switch_back_to_immigration_restores_behaviour(consulting):
    select_pack("immigration")
    apply_pack()
    assert "family-based" in get_intake_config().case_type_configs
    assert "court_filing" in get_routing_config().classes
    fail = PrivilegeCheck().run(_packet(restricted=True, external=True), QCConfig())
    assert "Privileged" in fail.reason
