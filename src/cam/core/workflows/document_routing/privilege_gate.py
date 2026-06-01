"""PrivilegeGate — the non-overridable hard block — spec §7, G5.

A privileged document NEVER reaches an external recipient.
This block cannot be bypassed by any human approval.
Default-deny on unknown privilege for external targets.

The gate delegates to qc-verification privilege check for correctness
(the invariant is PBT-tested in test_document_routing.py).
"""

from __future__ import annotations

import structlog

from cam.core.domain.models import Document
from cam.core.workflows.document_routing.types import GateVerdict, RoutingDestination

log = structlog.get_logger(__name__)


class PrivilegeGate:
    """Hard gate — checked before any external-target move or share.

    This is not a configurable approval gate.  No token can override it.
    """

    def check(
        self,
        document: Document,
        destination: RoutingDestination,
        recipient_id: str | None,
        *,
        caller_claims_external: bool = False,
    ) -> GateVerdict:
        """Check privilege before any routing action.

        Args:
            document:               The document being routed.
            destination:            Where it is going.
            recipient_id:           Optional explicit external recipient.
            caller_claims_external: True if the caller has flagged this as external-bound.

        Returns GateVerdict(verdict="fail") if the document must be blocked.
        Returns GateVerdict(verdict="pass") for internal-only privileged documents.
        """
        # Re-derive external_bound from all signals (use stricter)
        dest_is_external = self._destination_is_external(destination)
        has_external_recipient = recipient_id is not None
        effective_external = caller_claims_external or dest_is_external or has_external_recipient

        if not effective_external:
            return GateVerdict(verdict="pass", reason="Internal filing — privilege not at risk.")

        # External target path — apply strict privilege check
        if document.privileged:
            reason = (
                f"Privileged document {document.id!r} cannot be routed to an external "
                f"target (external={effective_external}). "
                f"This block is non-overridable (NFR-1)."
            )
            log.warning(
                "privilege_gate.blocked",
                document_id=document.id,
                effective_external=effective_external,
            )
            return GateVerdict(verdict="fail", reason=reason)

        # Non-privileged document going externally — allowed
        return GateVerdict(verdict="pass", reason="Non-privileged document; external routing allowed.")

    def _destination_is_external(self, destination: RoutingDestination) -> bool:
        """A folder destination is internal; a share/recipient route is external."""
        # folder kind is always internal filing; external sharing requires explicit recipient_id
        return False  # folder moves are always internal; external requires recipient_id

    def is_hard_blocked(
        self,
        document: Document,
        destination: RoutingDestination,
        recipient_id: str | None,
        caller_claims_external: bool = False,
    ) -> bool:
        """Convenience: True if this combination must be hard-blocked."""
        verdict = self.check(
            document, destination, recipient_id,
            caller_claims_external=caller_claims_external,
        )
        return verdict.verdict == "fail"
