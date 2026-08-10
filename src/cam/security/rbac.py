"""RBAC model — roles, permissions, principal, default-deny authorisation.

Permission matrix is a declarative dict (not scattered `if` statements).
Any (role, permission) pair not explicitly granted is denied.

The concrete grant set is flagged ASSUMPTION (confirm) — the matrix below
is a working default pending firm sign-off (spec §13.3, PTD §12).
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum


class Role(StrEnum):
    """System roles.  ASSUMPTION (confirm): exact set pending firm sign-off."""

    ATTORNEY = "attorney"
    PARALEGAL = "paralegal"
    INTAKE_COORDINATOR = "intake_coordinator"
    OPERATIONS = "operations"
    AGENT_SERVICE = "agent_service"  # the constrained AI service identity
    ADMIN = "admin"


class Permission(StrEnum):
    """System permissions.  ASSUMPTION (confirm): exact set pending firm sign-off."""

    # Matter
    MATTER_READ = "matter.read"
    MATTER_WRITE = "matter.write"
    # Contact
    CONTACT_READ = "contact.read"
    CONTACT_WRITE = "contact.write"
    # Document
    DOCUMENT_READ = "document.read"
    DOCUMENT_WRITE = "document.write"
    DOCUMENT_GENERATE = "document.generate"
    DOCUMENT_ROUTE = "document.route"
    # Deadline
    DEADLINE_READ = "deadline.read"
    DEADLINE_WRITE = "deadline.write"
    # Communication / email
    EMAIL_DRAFT = "email.draft"
    EMAIL_SEND = "email.send"
    # Workflow
    WORKFLOW_RUN = "workflow.run"
    WORKFLOW_STATUS = "workflow.status"
    # Gate
    GATE_APPROVE = "gate.approve"
    # Audit
    AUDIT_EXPORT = "audit.export"
    # Admin
    RULE_ACTIVATE = "rule.activate"
    SECRET_ROTATE = "secret.rotate"


class Principal:
    """An authenticated actor with one or more roles."""

    def __init__(self, identity: str, roles: frozenset[Role]) -> None:
        self.identity = identity
        self.roles = roles

    def __repr__(self) -> str:
        return f"Principal({self.identity!r}, roles={set(self.roles)!r})"


# ---------------------------------------------------------------------------
# Permission matrix — default-deny; only entries here are allowed.
# ASSUMPTION (confirm): exact grants pending firm review.
# ---------------------------------------------------------------------------

_GRANTS: dict[Role, frozenset[Permission]] = {
    Role.ATTORNEY: frozenset(
        [
            Permission.MATTER_READ,
            Permission.MATTER_WRITE,
            Permission.CONTACT_READ,
            Permission.CONTACT_WRITE,
            Permission.DOCUMENT_READ,
            Permission.DOCUMENT_WRITE,
            Permission.DOCUMENT_GENERATE,
            Permission.DOCUMENT_ROUTE,
            Permission.DEADLINE_READ,
            Permission.DEADLINE_WRITE,
            Permission.EMAIL_DRAFT,
            Permission.EMAIL_SEND,
            Permission.WORKFLOW_RUN,
            Permission.WORKFLOW_STATUS,
            Permission.GATE_APPROVE,
            Permission.AUDIT_EXPORT,
        ]
    ),
    Role.PARALEGAL: frozenset(
        [
            Permission.MATTER_READ,
            Permission.MATTER_WRITE,
            Permission.CONTACT_READ,
            Permission.CONTACT_WRITE,
            Permission.DOCUMENT_READ,
            Permission.DOCUMENT_WRITE,
            Permission.DOCUMENT_GENERATE,
            Permission.DEADLINE_READ,
            Permission.DEADLINE_WRITE,
            Permission.EMAIL_DRAFT,
            Permission.WORKFLOW_RUN,
            Permission.WORKFLOW_STATUS,
        ]
    ),
    Role.INTAKE_COORDINATOR: frozenset(
        [
            Permission.MATTER_READ,
            Permission.CONTACT_READ,
            Permission.CONTACT_WRITE,
            Permission.DOCUMENT_READ,
            Permission.DEADLINE_READ,
            Permission.EMAIL_DRAFT,
            Permission.WORKFLOW_RUN,
            Permission.WORKFLOW_STATUS,
        ]
    ),
    Role.OPERATIONS: frozenset(
        [
            Permission.MATTER_READ,
            Permission.CONTACT_READ,
            Permission.DOCUMENT_READ,
            Permission.DEADLINE_READ,
            Permission.WORKFLOW_STATUS,
            Permission.AUDIT_EXPORT,
        ]
    ),
    Role.AGENT_SERVICE: frozenset(
        [
            # Minimal grant set — everything else is denied (PTD §12).
            Permission.MATTER_READ,
            Permission.CONTACT_READ,
            Permission.DOCUMENT_READ,
            Permission.DEADLINE_READ,
            Permission.EMAIL_DRAFT,
            Permission.WORKFLOW_RUN,
            Permission.WORKFLOW_STATUS,
        ]
    ),
    Role.ADMIN: frozenset(Permission),  # all permissions
}


def authorize(
    principal: Principal,
    permission: Permission,
    resource: str | None = None,
    resource_checker: Callable[[Principal, str], bool] | None = None,
) -> bool:
    """Return True only if the permission is explicitly granted to at least
    one of the principal's roles (default-deny).

    Args:
        principal: The actor requesting access.
        permission: The permission being checked.
        resource: Optional resource identifier for scoped checks.
        resource_checker: Optional callable for resource-level scoping;
            called only after role-level grant is confirmed.
    """
    for role in principal.roles:
        if permission in _GRANTS.get(role, frozenset()):
            if resource and resource_checker:
                return resource_checker(principal, resource)
            return True
    return False
