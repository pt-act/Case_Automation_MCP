"""extraction_confidence check — fields below threshold flagged."""

from __future__ import annotations

from cam.core.services.qc.types import (
    WARN_OR_FAIL_OR_PASS,
    CheckDescriptor,
    CheckResult,
    QCConfig,
    SkipReason,
    Verdict,
)


class ExtractionConfidenceCheck:
    id = "extraction_confidence"
    version = "1.0"
    applies_to = None
    severity_policy = WARN_OR_FAIL_OR_PASS
    required_inputs = frozenset(["extracted_fields"])

    def describe(self) -> CheckDescriptor:
        return CheckDescriptor(
            check_id=self.id, version=self.version,
            description=(
                "Extracted fields above fail_floor (fail if below), "
                "above warn_threshold (warn if between floor and threshold)."
            ),
            applies_to=["all"],
            required_inputs=list(self.required_inputs),
        )

    def run(self, packet, cfg: QCConfig) -> CheckResult:
        if not packet.extracted_fields:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.SKIPPED,
                reason="No extracted fields in packet.",
                skip_reason=SkipReason.FIELD_ABSENT,
            )

        fail_floor = cfg.extraction_fail_floor
        warn_threshold = cfg.extraction_warn_threshold

        failed: list[str] = []
        warned: list[str] = []

        for field in packet.extracted_fields:
            conf = field.confidence
            if conf < fail_floor:
                failed.append(f"{field.name} ({conf:.2f})")
            elif conf < warn_threshold:
                warned.append(f"{field.name} ({conf:.2f})")

        if failed:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.FAIL,
                reason=f"Fields below fail_floor ({fail_floor}): {failed}",
                evidence={"failed_fields": failed},
            )
        if warned:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.WARN,
                reason=f"Fields below warn_threshold ({warn_threshold}): {warned}",
                evidence={"warned_fields": warned},
            )
        return CheckResult(
            check_id=self.id, check_version=self.version,
            verdict=Verdict.PASS, reason="All extracted fields meet confidence thresholds.",
        )
