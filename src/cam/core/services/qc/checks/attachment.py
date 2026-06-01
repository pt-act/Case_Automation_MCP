"""attachment_integrity check — referenced attachments present + correct checksum/version."""

from __future__ import annotations

from cam.core.services.qc.types import (
    CheckDescriptor,
    CheckResult,
    QCConfig,
    SkipReason,
    Verdict,
    WARN_OR_FAIL_OR_PASS,
)


class AttachmentIntegrityCheck:
    id = "attachment_integrity"
    version = "1.0"
    applies_to = None
    severity_policy = WARN_OR_FAIL_OR_PASS
    required_inputs = frozenset(["attachment_refs", "documents"])

    def describe(self) -> CheckDescriptor:
        return CheckDescriptor(
            check_id=self.id, version=self.version,
            description="Referenced attachments present with correct checksum and version.",
            applies_to=["all"],
            required_inputs=list(self.required_inputs),
        )

    def run(self, packet, cfg: QCConfig) -> CheckResult:
        if not packet.attachment_refs:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.SKIPPED,
                reason="No attachment references in packet.",
                skip_reason=SkipReason.FIELD_ABSENT,
            )

        doc_by_name = {d.name: d for d in packet.documents}
        failures: list[str] = []
        warnings: list[str] = []

        for ref in packet.attachment_refs:
            doc = doc_by_name.get(ref.referenced_name)
            if doc is None:
                failures.append(f"Attachment {ref.referenced_name!r} not found in packet.")
                continue
            if ref.expected_checksum and doc.checksum != ref.expected_checksum:
                failures.append(
                    f"Attachment {ref.referenced_name!r} checksum mismatch."
                )
            if ref.expected_version is not None and doc.version > ref.expected_version:
                warnings.append(
                    f"Attachment {ref.referenced_name!r} version {doc.version} "
                    f"newer than referenced {ref.expected_version}."
                )
            elif ref.expected_version is not None and doc.version != ref.expected_version:
                failures.append(
                    f"Attachment {ref.referenced_name!r} version mismatch: "
                    f"expected {ref.expected_version}, got {doc.version}."
                )

        if failures:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.FAIL,
                reason=f"Attachment failures: {failures}",
                evidence={"failures": failures, "warnings": warnings},
            )
        if warnings:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.WARN,
                reason=f"Attachment warnings: {warnings}",
                evidence={"warnings": warnings},
            )
        return CheckResult(
            check_id=self.id, check_version=self.version,
            verdict=Verdict.PASS, reason="All attachments present and verified.",
        )
