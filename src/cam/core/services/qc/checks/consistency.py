"""consistency check — client name / matter ref identical across packet."""

from __future__ import annotations

from cam.core.services.qc.types import (
    WARN_OR_FAIL_OR_PASS,
    CheckDescriptor,
    CheckResult,
    QCConfig,
    Verdict,
)


def _normalise(s: str) -> str:
    return " ".join(s.lower().split())


class ConsistencyCheck:
    id = "consistency"
    version = "1.0"
    applies_to = None
    severity_policy = WARN_OR_FAIL_OR_PASS
    required_inputs = frozenset(["matter", "documents", "communications"])

    def describe(self) -> CheckDescriptor:
        return CheckDescriptor(
            check_id=self.id, version=self.version,
            description="Client name and matter ref consistent across packet artefacts.",
            applies_to=["all"],
            required_inputs=list(self.required_inputs),
        )

    def run(self, packet, cfg: QCConfig) -> CheckResult:
        mismatches: list[str] = []
        normalised_diffs: list[str] = []

        canonical_name = packet.matter.client.name if packet.matter.client else None

        # Check all documents share the same matter_id
        for doc in packet.documents:
            if doc.matter_id != packet.matter.id:
                mismatches.append(
                    f"Document {doc.id!r} matter_id={doc.matter_id!r} != {packet.matter.id!r}"
                )

        # Check communication participants contain the matter client
        if canonical_name:
            for comm in packet.communications:
                for participant in comm.participants:
                    if participant.id == packet.matter.client.id:
                        if participant.name != canonical_name:
                            n1 = _normalise(participant.name)
                            n2 = _normalise(canonical_name)
                            if n1 != n2:
                                mismatches.append(
                                    f"Name mismatch in comm {comm.id!r}: "
                                    f"{participant.name!r} vs {canonical_name!r}"
                                )
                            else:
                                normalised_diffs.append(
                                    f"Near-match in comm {comm.id!r}: "
                                    f"{participant.name!r} normalised to {canonical_name!r}"
                                )

        if mismatches:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.FAIL,
                reason=f"Consistency failures: {mismatches}",
                evidence={"mismatches": mismatches},
            )
        if normalised_diffs:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.WARN,
                reason=f"Near-match differences normalised: {normalised_diffs}",
                evidence={"normalised_diffs": normalised_diffs},
            )
        return CheckResult(
            check_id=self.id, check_version=self.version,
            verdict=Verdict.PASS, reason="All artefacts consistent.",
        )
