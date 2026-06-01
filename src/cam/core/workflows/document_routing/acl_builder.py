"""AclBuilder — ACL ⊆ matter allowed-principal set — spec G4.

Result is always a strict subset of the matter's allowed principals.
Any principal outside the allowed set is dropped and the drop is audited.
Empty allowed set → empty ACL (no default-open).
"""

from __future__ import annotations

import json
import structlog

from cam.core.domain.models import ACL, Matter
from cam.core.workflows.document_routing.config import RoutingConfig, get_routing_config

log = structlog.get_logger(__name__)


def _parse_principals(external_ids: dict) -> set[str]:
    """Parse the allowed_principals set from Matter.external_ids.

    Preferred encoding: JSON array in key ``allowed_principals_json``.
    Legacy fallback: comma-separated string in key ``allowed_principals``.
    """
    if "allowed_principals_json" in external_ids:
        try:
            raw = external_ids["allowed_principals_json"]
            principals = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(principals, list):
                return {str(p).strip() for p in principals if str(p).strip()}
        except (json.JSONDecodeError, TypeError):
            pass

    # Legacy comma-separated fallback
    raw_csv = external_ids.get("allowed_principals", "")
    return {p.strip() for p in raw_csv.split(",") if p.strip()}


class AclBuilder:
    def build(
        self,
        matter: Matter,
        class_name: str,
        config: RoutingConfig | None = None,
        audit_fn: object | None = None,
    ) -> ACL:
        """Build a least-privilege ACL for a document filing.

        ACL = matter_allowed_set ∩ class_policy_set.
        Dropped principals are logged (id only, no PII).
        """
        cfg = config or get_routing_config()

        # Matter's allowed principal set — supports both JSON array and legacy comma-string.
        # JSON array ("allowed_principals_json") is preferred; comma-string is a fallback
        # for data that predates the encoding fix.  Comma-string is FRAGILE: any principal
        # ID containing a comma will split incorrectly.  Migrate external data to JSON.
        matter_allowed = _parse_principals(matter.external_ids)
        # Always include the responsible attorney if present
        if matter.responsible:
            matter_allowed.add(matter.responsible)

        # Class permission policy narrows further
        class_policy = set(cfg.class_permission_policies.allowed_principals_for(class_name))

        if not matter_allowed:
            return ACL(principals=[], permission="read", external=False)

        # Intersection
        if class_policy:
            granted = matter_allowed & class_policy
        else:
            granted = matter_allowed  # no class-level restriction

        # Log any principals that were in class_policy but not in matter_allowed
        dropped = class_policy - matter_allowed if class_policy else set()
        for p in dropped:
            log.warning("acl_builder.principal_dropped", principal_id=p, class_name=class_name)

        return ACL(
            principals=sorted(granted),
            permission="read",
            external=False,
        )
