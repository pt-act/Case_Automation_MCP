"""G6 — qc.verify tool, audit, run attachment focused tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cam.core.domain.models import Contact, Matter
from cam.core.services.qc.checks import register_all
from cam.core.services.qc.packet import PacketKind, VerificationPacket
from cam.core.services.qc.registry import clear_registry
from cam.core.services.qc.tool import QCVerifyInput, tool_qc_verify
from cam.core.services.qc.types import Aggregate, QCConfig


NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _setup():
    clear_registry()
    register_all()


def _packet(kind: str = "generic") -> VerificationPacket:
    client = Contact(id="c1", source="crm", name="Ana Garcia", external_ids={})
    matter = Matter(
        id="m1", source="case", reference="REF-001", title="T",
        status="open", client=client, opened_at=NOW, external_ids={},
    )
    return VerificationPacket(
        packet_id="p1", kind=PacketKind(kind), matter=matter, now=NOW,
    )


# a. Tool happy-path returns QCReport
async def test_tool_returns_report() -> None:
    inp = QCVerifyInput(packet=_packet())
    report = await tool_qc_verify(inp)
    assert report.packet_id == "p1"
    assert report.aggregate in (Aggregate.PASS, Aggregate.PASS_WITH_WARNINGS, Aggregate.BLOCK)
    assert report.config_fingerprint


# b. Audit record written exactly once per call
async def test_audit_written_once() -> None:
    audit_calls: list[dict] = []

    async def fake_audit(**kwargs):
        audit_calls.append(kwargs)

    inp = QCVerifyInput(packet=_packet())
    await tool_qc_verify(inp, audit_fn=fake_audit)
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "qc.verify"


# c. Audit record contains no raw PII values
async def test_audit_no_pii() -> None:
    audit_calls: list[dict] = []

    async def fake_audit(**kwargs):
        audit_calls.append(kwargs)

    inp = QCVerifyInput(packet=_packet())
    await tool_qc_verify(inp, audit_fn=fake_audit)
    record = audit_calls[0]
    # The inputs/outputs should not contain raw PII strings
    record_str = str(record)
    assert "Ana Garcia" not in record_str  # client name not in audit inputs/outputs


# d. Report attached to run store
async def test_report_attached_to_run() -> None:
    attached: list[dict] = []

    class FakeRunStore:
        async def attach_qc_report(self, run_id, report_dict):
            attached.append({"run_id": run_id, "report": report_dict})

    packet = _packet()
    packet = packet.model_copy(update={"run_id": "run-abc"})
    inp = QCVerifyInput(packet=packet)
    await tool_qc_verify(inp, run_store=FakeRunStore())
    assert len(attached) == 1
    assert attached[0]["run_id"] == "run-abc"


# e. Config fingerprint changes when threshold changes
async def test_config_fingerprint_changes_on_threshold() -> None:
    from cam.core.services.qc.types import compute_config_fingerprint

    cfg1 = QCConfig(extraction_warn_threshold=0.80)
    cfg2 = QCConfig(extraction_warn_threshold=0.90)
    assert compute_config_fingerprint(cfg1) != compute_config_fingerprint(cfg2)


# f. Fingerprint is deterministic
async def test_config_fingerprint_deterministic() -> None:
    from cam.core.services.qc.types import compute_config_fingerprint

    cfg = QCConfig(extraction_warn_threshold=0.80)
    assert compute_config_fingerprint(cfg) == compute_config_fingerprint(cfg)


# g. qc_checklist prompt lists applicable checks
def test_checklist_prompt_lists_checks() -> None:
    from cam.core.services.qc.prompts import render_qc_checklist

    prompt = render_qc_checklist("generic")
    assert "privilege" in prompt
    assert "completeness" in prompt
    assert "No client data" in prompt
