"""recipient_integrity check — every external recipient ∈ matter.participants."""

from __future__ import annotations

from cam.core.services.qc.packet import VerificationPacket
from cam.core.services.qc.types import (
    WARN_OR_FAIL_OR_PASS,
    CheckDescriptor,
    CheckResult,
    QCConfig,
    Verdict,
)


class RecipientIntegrityCheck:
    id = "recipient_integrity"
    version = "1.0"
    applies_to = frozenset(["email_send", "document_route"])
    severity_policy = WARN_OR_FAIL_OR_PASS
    required_inputs = frozenset(["intended_recipients", "matter"])

    def describe(self) -> CheckDescriptor:
        return CheckDescriptor(
            check_id=self.id, version=self.version,
            description="Every intended recipient is a matter participant with correct role.",
            applies_to=list(self.applies_to),
            required_inputs=list(self.required_inputs),
        )

    def run(self, packet: VerificationPacket, cfg: QCConfig) -> CheckResult:
        if not packet.intended_recipients:
            # On a send packet, empty recipients is a fail (nothing to verify → not a pass)
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.FAIL,
                reason="No intended recipients found on a send-type packet.",
                evidence={"packet_kind": packet.kind},
            )

        # Build participant set from matter client + any explicit participant list
        participant_ids = {packet.matter.client.id} if packet.matter.client else set()

        unknown: list[str] = []
        role_mismatches: list[str] = []

        for recipient in packet.intended_recipients:
            if recipient.id not in participant_ids:
                unknown.append(recipient.id)
            elif recipient.role and recipient.role not in ("client", "attorney", "representative"):
                role_mismatches.append(
                    f"{recipient.id!r} has unexpected role {recipient.role!r}"
                )

        if unknown:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.FAIL,
                reason=f"Recipients not in matter participants: {unknown}",
                evidence={"unknown_recipient_ids": unknown},
            )
        if role_mismatches:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.WARN,
                reason=f"Role mismatches: {role_mismatches}",
                evidence={"role_mismatches": role_mismatches},
            )
        return CheckResult(
            check_id=self.id, check_version=self.version,
            verdict=Verdict.PASS, reason="All recipients are matter participants.",
        )
