"""Consulting/advisory reference pack — assembly.

A self-contained pack written from scratch (it shares no values with the
immigration pack). Its only job is to prove the seam generalises: the engine
runs unchanged, the vocabulary and policy differ, and no core file is touched.

Confidentiality here is "Client-Confidential" rather than attorney-client
"Privileged" — same fail-closed, never-warn engine mechanics, different label.
Inherits the full engine PII floor and adds nothing (pii_patterns=()).
"""

from __future__ import annotations

from cam.core.services.deadline.types import DeadlineRule, Offset
from cam.core.services.qc.packet import PacketKind
from cam.core.workflows.intake.config import DedupeConfig
from cam.core.workflows.intake.types import CaseTypeConfig, TaskTemplate
from cam.packs.base import DomainPack, RestrictionPolicy, Terminology

_TERMINOLOGY = Terminology(
    matter="Engagement",
    matter_plural="Engagements",
    contact="Client",
    contact_plural="Clients",
    deadline="Key Date",
    document="Document",
    restriction_label="Client-Confidential",
    practice_noun="service line",
)

_CASE_TYPES = {
    "advisory": CaseTypeConfig(
        case_type="advisory",
        required_fields=["full_name", "email"],
        opening_tasks=[
            TaskTemplate(title="Scope advisory engagement", assignee_role="consultant", sort=1),
            TaskTemplate(title="Conflict check", assignee_role="partner", sort=2),
            TaskTemplate(title="Open engagement in system", assignee_role="consultant", sort=3),
        ],
        deadline_rule_ids=["deliverable_due"],
        welcome_template_id="consulting_welcome",
    ),
    "audit": CaseTypeConfig(
        case_type="audit",
        required_fields=["full_name", "email"],
        opening_tasks=[
            TaskTemplate(
                title="Collect prior-year working papers",
                assignee_role="consultant",
                sort=1,
            ),
            TaskTemplate(title="Independence check", assignee_role="partner", sort=2),
        ],
        deadline_rule_ids=[],
        welcome_template_id="default_welcome",
    ),
}

_DEADLINE_RULES: tuple[DeadlineRule, ...] = (
    DeadlineRule(
        rule_id="deliverable_due",
        jurisdiction="US",
        practice_area="advisory",
        trigger="engagement_start_date",
        offset=Offset(business_days=30),
        adjust="next_business_day",
        description="Advisory deliverable due (illustrative; ASSUMPTION (confirm)).",
        assumption_unconfirmed=True,
    ),
)

# A pack must define the engine-required roles (at minimum agent_service).
_RBAC_ROLES = {
    "partner": frozenset(
        {
            "matter.read", "matter.write", "contact.read", "contact.write",
            "document.read", "document.write", "document.generate", "document.route",
            "deadline.read", "deadline.write", "email.draft", "email.send",
            "workflow.run", "workflow.status", "gate.approve", "audit.export",
        }
    ),
    "consultant": frozenset(
        {
            "matter.read", "matter.write", "contact.read", "contact.write",
            "document.read", "document.write", "document.generate",
            "deadline.read", "deadline.write", "email.draft",
            "workflow.run", "workflow.status",
        }
    ),
    "operations": frozenset(
        {"matter.read", "contact.read", "document.read", "deadline.read",
         "workflow.status", "audit.export"}
    ),
    "agent_service": frozenset(
        {"matter.read", "contact.read", "document.read", "deadline.read",
         "email.draft", "workflow.run", "workflow.status"}
    ),
    "admin": frozenset(
        {
            "matter.read", "matter.write", "contact.read", "contact.write",
            "document.read", "document.write", "document.generate", "document.route",
            "deadline.read", "deadline.write", "email.draft", "email.send",
            "workflow.run", "workflow.status", "gate.approve", "audit.export",
            "rule.activate", "secret.rotate",
        }
    ),
}


CONSULTING_PACK = DomainPack(
    name="consulting",
    version="0.1.0",
    terminology=_TERMINOLOGY,
    case_types=_CASE_TYPES,
    default_case_type="advisory",
    dedupe=DedupeConfig(),
    deadline_rules=_DEADLINE_RULES,
    document_classes=(
        "engagement_letter", "report", "correspondence",
        "working_papers", "internal_memo", "unknown",
    ),
    packet_kinds=tuple(k.value for k in PacketKind),
    consistency_identifiers=("client_id", "engagement_ref"),
    restriction=RestrictionPolicy(label="Client-Confidential", default_restricted=True),
    rbac_roles=_RBAC_ROLES,
    pii_patterns=(),   # inherits the full engine PII floor; adds nothing
    template_ids=frozenset({"consulting_welcome", "default_welcome"}),
    feature_flags={},
)
