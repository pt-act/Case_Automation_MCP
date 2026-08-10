"""privilege check — no privileged doc to an external recipient.

This is the highest-stakes check.  It NEVER emits warn — only pass or fail.
Fail-closed: any ambiguity about external_bound is treated as external.
A privileged doc in an external-bound packet → fail, no exceptions.
"""

from __future__ import annotations

from cam.core.services.qc.packet import VerificationPacket
from cam.core.services.qc.types import (
    FAIL_OR_PASS,
    CheckDescriptor,
    CheckResult,
    QCConfig,
    Verdict,
)
from cam.packs.base import RestrictionPolicy


def _active_restriction() -> RestrictionPolicy | None:
    """Return the active domain pack's RestrictionPolicy, or None if no pack is
    active (the check then falls back to the immigration "Privileged" label and
    the engine-fixed fail-closed mechanics)."""
    try:
        from cam.packs.base import get_active_pack

        return get_active_pack().restriction
    except Exception:
        return None


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

    def run(self, packet: VerificationPacket, cfg: QCConfig) -> CheckResult:
        # Determine effective external_bound — use stricter of caller claim vs re-derived
        caller_claim = packet.external_bound
        # Re-derive: any recipient not in matter participants → external
        participant_ids = {packet.matter.client.id} if packet.matter.client else set()
        has_unknown_recipient = any(
            r.id not in participant_ids for r in packet.intended_recipients
        )
        effective_external = caller_claim or has_unknown_recipient

        # The confidentiality label comes from the active domain pack's
        # RestrictionPolicy ("Privileged" for immigration, "Client-Confidential"
        # for consulting). Fail-closed default if no pack is active.
        restriction = _active_restriction()
        label = restriction.label if restriction is not None else "Privileged"

        # Canonical confidentiality flag (`restricted`; `privileged` is its alias).
        restricted_docs = [d for d in packet.documents if d.restricted]
        if not restricted_docs:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.PASS,
                reason=f"No {label.lower()} documents in packet.",
            )

        # Engine-fixed rule: a restricted doc in an external-bound packet fails.
        # A pack's extra predicates may only TIGHTEN (add a fail), never relax.
        extra_block = bool(restriction is not None and restriction.extra_block(packet))
        if effective_external or extra_block:
            return CheckResult(
                check_id=self.id, check_version=self.version,
                verdict=Verdict.FAIL,
                reason=(
                    f"{label} document(s) cannot be routed to external recipients. "
                    f"effective_external={effective_external} "
                    f"(caller_claim={caller_claim}, re_derived={has_unknown_recipient})."
                ),
                evidence={
                    "restricted_doc_count": len(restricted_docs),
                    "effective_external": effective_external,
                },
            )

        return CheckResult(
            check_id=self.id, check_version=self.version,
            verdict=Verdict.PASS,
            reason=f"{label} documents are internal only.",
        )
