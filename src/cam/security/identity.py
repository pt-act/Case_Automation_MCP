"""Constrained agent service identity — PTD §12, spec §4.6."""

from __future__ import annotations

from cam.security.rbac import Permission, Principal, Role, authorize

AGENT_SERVICE_PRINCIPAL = Principal(
    identity="agent_service",
    roles=frozenset([Role.AGENT_SERVICE]),
)

# Convenience: the explicit grant set for documentation and testing.
AGENT_GRANTS: frozenset[Permission] = frozenset(
    [
        Permission.MATTER_READ,
        Permission.CONTACT_READ,
        Permission.DOCUMENT_READ,
        Permission.DEADLINE_READ,
        Permission.EMAIL_DRAFT,
        Permission.WORKFLOW_RUN,
        Permission.WORKFLOW_STATUS,
    ]
)


def agent_can(permission: Permission) -> bool:
    """Check whether the agent service identity holds a permission."""
    return authorize(AGENT_SERVICE_PRINCIPAL, permission)
