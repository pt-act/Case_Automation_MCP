"""QC recipient-integrity gate input + gate wiring — spec G4.

Consumes qc-verification; does not own the check internals.
fail → run parked blocked; warn → annotated; pass → proceed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from cam.core.domain.models import Contact, Matter
from cam.core.services.qc.checks import register_all
from cam.core.services.qc.packet import PacketKind, VerificationPacket
from cam.core.services.qc.registry import clear_registry, run_checks
from cam.core.services.qc.tool import QCVerifyInput, tool_qc_verify
from cam.core.services.qc.types import Aggregate, QCConfig

log = structlog.get_logger(__name__)


async def run_recipient_integrity_qc(
    matter: Matter,
    recipients: list[Contact],
    run_id: str,
    audit_fn: Any | None = None,
) -> str:
    """Run the recipient_integrity check against the draft recipients.

    Returns: "pass" | "warn" | "fail"
    Callers treat "fail" as a hard block.
    """
    packet = VerificationPacket(
        packet_id=f"qc-{run_id}",
        run_id=run_id,
        kind=PacketKind.EMAIL_SEND,
        matter=matter,
        intended_recipients=recipients,
        now=datetime.now(tz=timezone.utc),
        config_snapshot=QCConfig(),
        external_bound=True,  # status update is client-facing
    )

    inp = QCVerifyInput(packet=packet, checks=["recipient_integrity"])
    try:
        report = await tool_qc_verify(inp, audit_fn=audit_fn)
        result = report.aggregate
        if result == Aggregate.BLOCK:
            log.warning("status_update.qc_fail", run_id=run_id, matter_id=matter.id)
            return "fail"
        if result == Aggregate.PASS_WITH_WARNINGS:
            log.info("status_update.qc_warn", run_id=run_id)
            return "warn"
        return "pass"
    except Exception as exc:
        log.error("status_update.qc_error", run_id=run_id, error=str(exc))
        return "fail"  # fail-closed
