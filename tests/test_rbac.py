"""G7 — RBAC model & agent identity focused tests."""

from __future__ import annotations

from cam.security.identity import AGENT_GRANTS, agent_can
from cam.security.rbac import Permission, Principal, Role, authorize


def principal(*roles: Role) -> Principal:
    return Principal("test-user", frozenset(roles))


# 1. Granted pair allowed
def test_attorney_can_approve_gate() -> None:
    p = principal(Role.ATTORNEY)
    assert authorize(p, Permission.GATE_APPROVE) is True


# 2. Ungranted denied
def test_intake_coordinator_cannot_approve_gate() -> None:
    p = principal(Role.INTAKE_COORDINATOR)
    assert authorize(p, Permission.GATE_APPROVE) is False


# 3. Unknown role denied (empty roles)
def test_empty_roles_denied() -> None:
    p = Principal("nobody", frozenset())
    assert authorize(p, Permission.MATTER_READ) is False


# 4. Resource-scoped check
def test_resource_scoped_allow() -> None:
    p = principal(Role.ATTORNEY)

    def checker(principal: Principal, resource: str) -> bool:
        return resource == "matter/m1"

    assert authorize(p, Permission.MATTER_READ, resource="matter/m1", resource_checker=checker)


def test_resource_scoped_deny() -> None:
    p = principal(Role.ATTORNEY)

    def checker(principal: Principal, resource: str) -> bool:
        return resource == "matter/m1"

    assert not authorize(p, Permission.MATTER_READ, resource="matter/m2", resource_checker=checker)


# 5. Agent service principal only holds its minimal grant set
def test_agent_can_read_matter() -> None:
    assert agent_can(Permission.MATTER_READ) is True


def test_agent_cannot_send_email() -> None:
    assert agent_can(Permission.EMAIL_SEND) is False


def test_agent_cannot_approve_gate() -> None:
    assert agent_can(Permission.GATE_APPROVE) is False


def test_agent_cannot_export_audit() -> None:
    assert agent_can(Permission.AUDIT_EXPORT) is False


def test_agent_grants_are_minimal() -> None:
    all_permissions = set(Permission)
    non_granted = all_permissions - AGENT_GRANTS
    # agent must not hold permissions outside its explicit grant set
    for perm in non_granted:
        assert not agent_can(perm), f"Agent should not hold {perm}"


# 6. Admin role holds all permissions
def test_admin_has_all_permissions() -> None:
    p = principal(Role.ADMIN)
    for perm in Permission:
        assert authorize(p, perm), f"Admin should hold {perm}"
