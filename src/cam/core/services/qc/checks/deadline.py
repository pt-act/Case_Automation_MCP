"""deadline_sanity check — due dates plausible, not past-due at create.

Uses packet.now exclusively — never datetime.now() internally (determinism).
"""

from __future__ import annotations

from datetime import timedelta

from cam.core.services.qc.types import (
    WARN_OR_FAIL_OR_PASS,
    CheckDescriptor,
    CheckResult,
    QCConfig,
    SkipReason,
    Verdict,
)


class DeadlineSanityCheck:
    id = "deadline_sanity"
    version = "1.0"
    applies_to = None
    severity_policy = WARN_OR_FAIL_OR_PASS
    required_inputs = frozenset(["deadlines", "now"])

    def describe(self) -> CheckDescriptor:
        return CheckDescriptor(
            check_id=self.id, version=self.version,
            description="Deadlines are plausible: not past-due and within configured horizon.",
            applies_to=["all"],
            required_inputs=list(self.required_inputs),
        )

    def run(self, packet, cfg: QCConfig) -> CheckResult:
        if not packet.deadlines:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.SKIPPED,
                reason="No deadlines in packet.",
                skip_reason=SkipReason.FIELD_ABSENT,
            )

        now = packet.now
        horizon = now + timedelta(days=cfg.deadline_horizon_days)
        tight_window = now + timedelta(days=cfg.deadline_tight_window_days)

        past_due: list[str] = []
        beyond_horizon: list[str] = []
        tight: list[str] = []

        for dl in packet.deadlines:
            if dl.due_at < now:
                past_due.append(f"{dl.id} (due={dl.due_at.isoformat()})")
            elif dl.due_at > horizon:
                beyond_horizon.append(f"{dl.id} (due={dl.due_at.isoformat()})")
            elif dl.due_at <= tight_window:
                tight.append(f"{dl.id} (due={dl.due_at.isoformat()})")

        if past_due or beyond_horizon:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.FAIL,
                reason=f"Past-due: {past_due}; beyond horizon: {beyond_horizon}",
                evidence={"past_due": past_due, "beyond_horizon": beyond_horizon},
            )
        if tight:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.WARN,
                reason=f"Deadlines approaching tight window: {tight}",
                evidence={"tight": tight},
            )
        return CheckResult(
            check_id=self.id, check_version=self.version,
            verdict=Verdict.PASS, reason="All deadlines within plausible range.",
        )
