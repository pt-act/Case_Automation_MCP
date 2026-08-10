"""completeness check — required template vars resolved + matter fields present."""

from __future__ import annotations

from cam.core.services.qc.packet import VerificationPacket
from cam.core.services.qc.types import (
    WARN_OR_FAIL_OR_PASS,
    CheckDescriptor,
    CheckResult,
    QCConfig,
    SkipReason,
    Verdict,
)


class CompletenessCheck:
    id = "completeness"
    version = "1.0"
    applies_to = None  # all kinds
    severity_policy = WARN_OR_FAIL_OR_PASS
    required_inputs = frozenset(["template_bindings", "matter"])

    def describe(self) -> CheckDescriptor:
        return CheckDescriptor(
            check_id=self.id,
            version=self.version,
            description="Required template variables resolved; required matter fields present.",
            applies_to=["all"],
            required_inputs=list(self.required_inputs),
            severity_policy="warn_or_fail_or_pass",
        )

    def run(self, packet: VerificationPacket, cfg: QCConfig) -> CheckResult:
        missing_vars: list[str] = []
        missing_fields: list[str] = []

        # Template binding completeness
        if not packet.template_bindings:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.SKIPPED,
                reason="No template bindings in packet.",
                skip_reason=SkipReason.FIELD_ABSENT,
            )

        for binding in packet.template_bindings:
            for var in binding.required_vars:
                val = binding.resolved_vars.get(var)
                if val is None or str(val).strip() == "":
                    missing_vars.append(f"{binding.template_name}.{var}")

        # Required matter field checks
        matter = packet.matter
        for field_name in ("id", "reference", "title", "status"):
            if not getattr(matter, field_name, None):
                missing_fields.append(f"matter.{field_name}")
        if not matter.client or not matter.client.id:
            missing_fields.append("matter.client")

        if missing_vars or missing_fields:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.FAIL,
                reason=(
                    f"Missing required vars: {missing_vars}; "
                    f"missing matter fields: {missing_fields}"
                ),
                evidence={"missing_vars": missing_vars, "missing_matter_fields": missing_fields},
            )

        return CheckResult(
            check_id=self.id, check_version=self.version,
            verdict=Verdict.PASS, reason="All required vars and matter fields present.",
        )
