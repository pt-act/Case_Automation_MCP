"""privilege check — no privileged doc to an external recipient.

This is the highest-stakes check.  It NEVER emits warn — only pass or fail.
Fail-closed: any ambiguity about external_bound is treated as external.
A privileged doc in an external-bound packet → fail, no exceptions.
"""

from __future__ import annotations

from cam.core.services.qc.types import (
    CheckDescriptor,
    CheckResult,
    QCConfig,
    SeverityPolicy,
    Verdict,
    FAIL_OR_PASS,
)


class PrivilegeCheck:
    id = "privilege"
    version = "1.0"
    applies_to = None   # all packet kinds
    severity_policy = FAIL_OR_PASS   # NEVER warn
    required_inputs = frozenset(["documents", "intended_recipients", "matter"])

    def describe(self) -> CheckDescriptor:
        return CheckDescriptor(
            check_id=self.id, version=self.version,
            description=(
                "No privileged document reaches an external recipient. "
                "Fail-closed: ambiguous external_bound treated as external. "
                "Never emits warn."
            ),
            applies_to=["all"],
            required_inputs=list(self.required_inputs),
            severity_policy="fail_or_pass (never warn)",
        )

    def run(self, packet, cfg: QCConfig) -> CheckResult:
        # Determine effective external_bound — use stricter of caller claim vs re-derived
        caller_claim = packet.external_bound
        # Re-derive: any recipient not in matter participants → external
        participant_ids = {packet.matter.client.id} if packet.matter.client else set()
        has_unknown_recipient = any(
            r.id not in participant_ids for r in packet.intended_recipients
        )
        effective_external = caller_claim or has_unknown_recipient

        privileged_docs = [d for d in packet.documents if d.privileged]
        if not privileged_docs:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.PASS,
                reason="No privileged documents in packet.",
            )

        if effective_external:
            doc_ids = [d.id for d in privileged_docs]
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.FAIL,
                reason=(
                    f"Privileged document(s) cannot be routed to external recipients. "
                    f"effective_external={effective_external} "
                    f"(caller_claim={caller_claim}, re_derived={has_unknown_recipient})."
                ),
                evidence={
                    "privileged_doc_count": len(privileged_docs),
                    "effective_external": effective_external,
                },
            )

        return CheckResult(
            check_id=self.id, check_version=self.version,
            verdict=Verdict.PASS,
            reason="Privileged documents are internal only.",
        )
