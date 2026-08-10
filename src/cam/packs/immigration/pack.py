"""Immigration reference pack — assembly.

This pack is the single home for the immigration practice's domain values
(intake case types, the A-number identifier format, document-generation form
ids, the "Privileged" confidentiality label, PII additions). The engine core
holds no immigration value; consumers read these from the active pack (G6).

Engine-neutral structure (document classes, QC packet kinds, the RBAC
role/permission matrix) is still owned by the engine and merely *projected*
here — those carry no domain-specific value, so they are not duplicated.

The immigration-specific PII patterns (A-number, passport) live here as pack
*additions* on top of the engine floor (decision D-2).
"""

from __future__ import annotations

import re

from cam.core.services.deadline.types import DeadlineRule, Offset
from cam.core.services.qc.packet import PacketKind
from cam.core.workflows.document_routing.config import DOCUMENT_CLASSES
from cam.core.workflows.intake.config import DedupeConfig
from cam.core.workflows.intake.types import CaseTypeConfig, TaskTemplate
from cam.packs.base import DomainPack, RestrictionPolicy, Terminology
from cam.security.rbac import _GRANTS

# --- terminology -----------------------------------------------------------

_TERMINOLOGY = Terminology(
    matter="Matter",
    matter_plural="Matters",
    contact="Client",
    contact_plural="Clients",
    deadline="Deadline",
    document="Document",
    restriction_label="Privileged",   # the immigration confidentiality concept
    practice_noun="practice area",
)

# --- case types (the immigration intake configuration; moved out of core) --

_CASE_TYPES: dict[str, CaseTypeConfig] = {
    "family-based": CaseTypeConfig(
        case_type="family-based",
        required_fields=["full_name", "dob", "country_of_origin", "current_status", "email"],
        opening_tasks=[
            TaskTemplate(
                title="Collect I-130 petition documents",
                assignee_role="paralegal",
                sort=1,
            ),
            TaskTemplate(title="Conflict check", assignee_role="attorney", sort=2),
            TaskTemplate(title="Open matter in case system", assignee_role="paralegal", sort=3),
        ],
        deadline_rule_ids=["rfe_response"],
        welcome_template_id="family_based_welcome",
    ),
    "employment-based": CaseTypeConfig(
        case_type="employment-based",
        required_fields=["full_name", "dob", "country_of_origin", "current_status", "email"],
        opening_tasks=[
            TaskTemplate(title="Collect employer documentation", assignee_role="paralegal", sort=1),
            TaskTemplate(title="Conflict check", assignee_role="attorney", sort=2),
            TaskTemplate(title="Open matter in case system", assignee_role="paralegal", sort=3),
        ],
        deadline_rule_ids=[],
        welcome_template_id="employment_based_welcome",
    ),
    "other/uncategorised": CaseTypeConfig(
        case_type="other/uncategorised",
        required_fields=["full_name", "email"],
        opening_tasks=[
            TaskTemplate(title="Initial client consultation", assignee_role="attorney", sort=1),
            TaskTemplate(title="Open matter in case system", assignee_role="paralegal", sort=2),
        ],
        deadline_rule_ids=[],
        welcome_template_id="default_welcome",
    ),
}

# --- deadline rules (illustrative; all unconfirmed, matching today) --------
# Referenced by the family-based case type's deadline_rule_ids=["rfe_response"].

_DEADLINE_RULES: tuple[DeadlineRule, ...] = (
    DeadlineRule(
        rule_id="rfe_response",
        jurisdiction="US",
        practice_area="family-based",
        trigger="rfe_issued_date",
        offset=Offset(days=87),
        adjust="next_business_day",
        description="RFE response window (illustrative; ASSUMPTION (confirm)).",
        assumption_unconfirmed=True,
    ),
)

# --- RBAC roles (string projection of the existing default-deny matrix) ----

_RBAC_ROLES = {
    role.value: frozenset(p.value for p in perms) for role, perms in _GRANTS.items()
}

# --- PII additions (immigration-specific; on top of the engine baseline) ---

_PII_ADDITIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bA\d{8,9}\b"),            # A-number
    re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"),   # Passport (simplified)
)

# --- identifier format validators (read by the intake a_number validator) --

_IDENTIFIER_PATTERNS: dict[str, re.Pattern[str]] = {
    "a_number": re.compile(r"^A\d{8,9}$"),
}

# --- document-generation form id → template id (read by form.prefill) ------

_PREFILL_FORMS: dict[str, str] = {
    "I-130": "i130_petition",
    "I-485": "i485_adjustment",
    "N-400": "n400_naturalization",
    "G-28": "g28_representation",
}

# --- template ids referenced by the case types ----------------------------

_TEMPLATE_IDS = frozenset(ct.welcome_template_id for ct in _CASE_TYPES.values())


IMMIGRATION_PACK = DomainPack(
    name="immigration",
    version="1.0.0",
    terminology=_TERMINOLOGY,
    case_types=dict(_CASE_TYPES),
    default_case_type="other/uncategorised",
    dedupe=DedupeConfig(),
    deadline_rules=_DEADLINE_RULES,
    document_classes=tuple(sorted(DOCUMENT_CLASSES)),
    packet_kinds=tuple(k.value for k in PacketKind),
    consistency_identifiers=("a_number", "receipt_number"),
    restriction=RestrictionPolicy(label="Privileged", default_restricted=True),
    rbac_roles=_RBAC_ROLES,
    pii_patterns=_PII_ADDITIONS,
    template_ids=_TEMPLATE_IDS,
    identifier_patterns=_IDENTIFIER_PATTERNS,
    prefill_forms=_PREFILL_FORMS,
    feature_flags={},
)
