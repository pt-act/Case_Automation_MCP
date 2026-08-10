"""qc.verify MCP tool — risk tier read, audit before return — spec §4.1, G6."""

from __future__ import annotations

from typing import Any

import structlog

from cam.core.services.qc.packet import VerificationPacket
from cam.core.services.qc.registry import run_checks
from cam.core.services.qc.types import QCReport, compute_config_fingerprint

log = structlog.get_logger(__name__)


class QCVerifyInput:
    """Input to the qc.verify tool."""

    def __init__(
        self,
        packet: VerificationPacket,
        checks: list[str] | None = None,
    ) -> None:
        self.packet = packet
        self.checks = checks


async def tool_qc_verify(
    inp: QCVerifyInput,
    audit_fn: Any | None = None,
    run_store: Any | None = None,
) -> QCReport:
    """Run QC verification and return a QCReport.

    Risk tier: read (no artefact mutations).  Writes exactly one audit record
    before returning.  Attaches the report to the workflow run if run_store is
    provided.

    Raises:
        pydantic.ValidationError  — if the packet is malformed (only error surfaced).
    """
    packet = inp.packet
    cfg = packet.config_snapshot
    config_fingerprint = compute_config_fingerprint(cfg)

    report = run_checks(packet, inp.checks, cfg)

    # Audit before returning (NFR-2, spec §5 step 6)
    await _write_audit(
        audit_fn=audit_fn,
        packet=packet,
        report=report,
        config_fingerprint=config_fingerprint,
    )

    # Attach report to workflow run
    if run_store is not None:
        try:
            await _attach_to_run(run_store, packet.run_id, report)
        except Exception as exc:
            log.warning("qc.run_attach_failed", run_id=packet.run_id, error=str(exc))

    log.info(
        "qc.verify_complete",
        packet_id=packet.packet_id,
        run_id=packet.run_id,
        aggregate=report.aggregate,
        fail_count=sum(1 for r in report.results if r.verdict == "fail"),
    )

    return report


async def _write_audit(
    audit_fn: Any,
    packet: VerificationPacket,
    report: QCReport,
    config_fingerprint: str,
) -> None:
    if audit_fn is None:
        return
    try:
        await audit_fn(
            actor="qc_verification_service",
            action="qc.verify",
            inputs={
                "packet_id": packet.packet_id,
                "packet_kind": packet.kind,
                "checks_selected": report.checks_selected,
                "config_fingerprint": config_fingerprint,
            },
            outputs={
                "aggregate": report.aggregate,
                "fail_count": sum(1 for r in report.results if r.verdict == "fail"),
                "warn_count": sum(1 for r in report.results if r.verdict == "warn"),
                "skip_count": len(report.checks_skipped),
                "check_verdicts": {r.check_id: r.verdict for r in report.results},
            },
            run_id=packet.run_id,
        )
    except Exception as exc:
        log.warning("qc.audit_write_failed", error=str(exc))


async def _attach_to_run(run_store: Any, run_id: str | None, report: QCReport) -> None:
    if run_id is None:
        return
    # RunStore.attach_qc_report is a soft contract; graceful if not implemented.
    if hasattr(run_store, "attach_qc_report"):
        await run_store.attach_qc_report(run_id, report.model_dump())
