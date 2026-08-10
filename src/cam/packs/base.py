"""Domain-pack contract, registry, validation, and PII composition.

This is the seam that makes the engine domain-agnostic (spec: domain-packs).
A `DomainPack` bundles everything practice-specific; the engine reads domain
values, labels, and policy from the *active* pack and from no in-engine default.

Status
------
The contract, registry, validation, PII composition, and two reference packs
(immigration, consulting) live here. Consumers read domain-specific values from
the active pack (G6): intake case types + the a_number identifier format, the
deadline rule set, routing document classes, the QC restriction policy, the PII
additions, and the document-generation form map. No immigration value remains in
core — the `test_no_immigration_value_hardcoded_in_core` guard enforces this.
Engine-neutral structure (the RBAC role/permission matrix, the QC PacketKind
enum) is owned by the engine and merely projected by packs; it carries no
domain-specific value, so it is not duplicated into every pack.

Safety invariants (engine-owned; a pack may only TIGHTEN, never weaken):
  * Confidentiality gate  — fail-closed, never-warn (RestrictionPolicy below).
  * PII redaction floor    — `ENGINE_PII_BASELINE` is non-removable; packs add.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PII redaction floor — engine-owned, non-removable (decision D-2).
# These patterns are PII in EVERY domain. Packs add domain-specific patterns
# (e.g. the immigration pack adds A-number + passport) but can never remove one.
# ---------------------------------------------------------------------------

ENGINE_PII_BASELINE: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                       # SSN / ITIN
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                       # DOB (YYYY-MM-DD)
    re.compile(r"\b\d{2}/\d{2}/\d{4}\b"),                       # DOB (MM/DD/YYYY)
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),  # email
    re.compile(r"\b(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"),  # phone
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PackValidationError(Exception):
    """A pack failed load-time validation. Message is PII-free and names the
    failing surface. The server refuses to serve on this error (fail-closed)."""


class PackSelectionError(Exception):
    """No pack selected, or an unknown pack name. Refuse to serve — there is no
    implicit default (decision D-5)."""


# ---------------------------------------------------------------------------
# Terminology — display labels for the neutral entities + the confidentiality
# concept. Labels feed prompts/documents/emails/UI; they NEVER rename the code
# types. Every slot is required (no blank labels reaching client-facing output).
# ---------------------------------------------------------------------------


class Terminology(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter: str                 # "Matter" | "Case" | "Engagement" | "Claim"
    matter_plural: str
    contact: str                # "Client"
    contact_plural: str
    deadline: str               # "Deadline" | "Key Date"
    document: str               # "Document"
    restriction_label: str      # "Privileged" | "Client-Confidential" | "Restricted"
    practice_noun: str          # "practice area" | "service line"

    @field_validator("*")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("terminology slots must be non-empty strings")
        return v


# ---------------------------------------------------------------------------
# RestrictionPolicy — generalises the immigration privilege gate.
#
# The engine's mechanics are FIXED: a restricted document in an external-bound
# packet fails, never warns, and the check takes the stricter of the caller's
# claim vs the re-derived external-bound. A pack supplies the *label* and may
# add *extra predicates* that can only turn a pass into a fail. By construction
# the consuming QC check computes:  block = engine_fail OR policy.extra_block(ctx)
# so a predicate can never flip a fail to a pass (tighten-only, decision D-3).
# ---------------------------------------------------------------------------


class RestrictionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    label: str
    default_restricted: bool = True
    extra_predicates: tuple[Callable[[Any], bool], ...] = ()

    @field_validator("label")
    @classmethod
    def _label_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("restriction label must be non-empty")
        return v

    def extra_block(self, ctx: Any) -> bool:
        """True if any pack predicate independently demands a block (tighten-only)."""
        return any(bool(pred(ctx)) for pred in self.extra_predicates)


# ---------------------------------------------------------------------------
# DomainPack — the bundle. Frozen for immutability (attribute reassignment
# raises). One pack is active per deployment (decision D-1).
#
# Reused config types (intake/deadline) are referenced, not forked, per the
# generalisation plan. They are typed loosely here to keep this module free of
# heavy workflow imports; the reference packs supply the concrete instances.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainPack:
    name: str
    version: str
    terminology: Terminology
    case_types: dict[str, Any]              # {case_type: CaseTypeConfig}
    default_case_type: str
    dedupe: Any                             # DedupeConfig
    deadline_rules: tuple[Any, ...]         # (DeadlineRule, ...)
    document_classes: tuple[str, ...]
    packet_kinds: tuple[str, ...]
    consistency_identifiers: tuple[str, ...]
    restriction: RestrictionPolicy
    rbac_roles: dict[str, frozenset[str]]   # {role: {permission, ...}}
    pii_patterns: tuple[re.Pattern[str], ...]
    template_ids: frozenset[str]
    # Domain identifier format validators, e.g. immigration {"a_number": <regex>}.
    # A consumer validating that identifier reads the pattern from the active pack
    # instead of hard-coding it. Empty for packs with no special identifiers.
    identifier_patterns: dict[str, re.Pattern[str]] = field(default_factory=dict)
    # Document-generation form id → template id map, e.g. immigration
    # {"I-130": "i130_petition"}. Empty for packs with no prefill forms.
    prefill_forms: dict[str, str] = field(default_factory=dict)
    feature_flags: dict[str, bool] = field(default_factory=dict)


# Roles the engine requires any pack to define (the constrained AI identity is
# non-negotiable — a pack cannot delete it).
_ENGINE_REQUIRED_ROLES: frozenset[str] = frozenset({"agent_service"})


# ---------------------------------------------------------------------------
# Validation (fail-closed). Raises PackValidationError naming the failing
# surface; never leaks PII. select_pack runs this before a pack becomes active.
# ---------------------------------------------------------------------------


def validate_pack(pack: DomainPack) -> None:
    # 1. Completeness
    if not pack.name or not pack.version:
        raise PackValidationError("pack is missing a name or version")
    if not pack.case_types:
        raise PackValidationError(f"pack {pack.name!r}: case_types is empty")
    if pack.default_case_type not in pack.case_types:
        raise PackValidationError(
            f"pack {pack.name!r}: default_case_type "
            f"{pack.default_case_type!r} is not a defined case type"
        )
    if not pack.document_classes:
        raise PackValidationError(f"pack {pack.name!r}: document_classes is empty")
    if not pack.packet_kinds:
        raise PackValidationError(f"pack {pack.name!r}: packet_kinds is empty")
    if not pack.consistency_identifiers:
        raise PackValidationError(
            f"pack {pack.name!r}: consistency_identifiers is empty"
        )

    # 2. Restriction conformance — fail-closed default may never be relaxed.
    if pack.restriction.default_restricted is not True:
        raise PackValidationError(
            f"pack {pack.name!r}: restriction.default_restricted must be True "
            "(a pack may tighten the confidentiality gate, never weaken it)"
        )

    # 3. PII-floor preservation — composed set must contain every baseline pattern.
    composed = active_pii_patterns(pack)
    missing = [p.pattern for p in ENGINE_PII_BASELINE if p not in composed]
    if missing:
        raise PackValidationError(
            f"pack {pack.name!r}: PII baseline floor not preserved "
            f"({len(missing)} baseline pattern(s) missing)"
        )

    # 4. Referential integrity
    rule_ids = {getattr(r, "rule_id", None) for r in pack.deadline_rules}
    for ct_name, ct in pack.case_types.items():
        wt = getattr(ct, "welcome_template_id", None)
        if wt is not None and wt not in pack.template_ids:
            raise PackValidationError(
                f"pack {pack.name!r}: case type {ct_name!r} references unknown "
                f"welcome template {wt!r}"
            )
        for rid in getattr(ct, "deadline_rule_ids", []) or []:
            if rid not in rule_ids:
                raise PackValidationError(
                    f"pack {pack.name!r}: case type {ct_name!r} references unknown "
                    f"deadline rule {rid!r}"
                )
    missing_roles = _ENGINE_REQUIRED_ROLES - set(pack.rbac_roles)
    if missing_roles:
        raise PackValidationError(
            f"pack {pack.name!r}: missing engine-required RBAC role(s): "
            f"{sorted(missing_roles)}"
        )


def run_pack_conformance(pack: DomainPack) -> bool:
    """Conformance harness every pack must pass. Returns True or raises
    PackValidationError. Mirrors the connector contract harness."""
    validate_pack(pack)
    return True


# ---------------------------------------------------------------------------
# PII composition — baseline ∪ pack (additive only).
# ---------------------------------------------------------------------------


def active_pii_patterns(pack: DomainPack | None = None) -> list[re.Pattern[str]]:
    """The effective redaction set: engine baseline plus the active pack's
    additions. A pack can only add; the baseline is never removed."""
    pack = pack or get_active_pack()
    return list(ENGINE_PII_BASELINE) + list(pack.pii_patterns)


# ---------------------------------------------------------------------------
# Registry + selection. One active pack per process (v1). No implicit default.
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, DomainPack] = {}
_ACTIVE: DomainPack | None = None


def register_pack(pack: DomainPack) -> None:
    if pack.name in _REGISTRY and _REGISTRY[pack.name] is not pack:
        raise PackValidationError(
            f"a different pack is already registered under name {pack.name!r}"
        )
    _REGISTRY[pack.name] = pack


def get_active_pack() -> DomainPack:
    if _ACTIVE is None:
        raise PackSelectionError(
            "no active domain pack — call select_pack()/activate_configured_pack() "
            "at startup (CAM_DOMAIN_PACK is required)"
        )
    return _ACTIVE


def select_pack(name: str) -> DomainPack:
    """Validate and activate a registered pack. Refuses to serve on unknown name
    or invalid pack."""
    global _ACTIVE
    pack = _REGISTRY.get(name)
    if pack is None:
        raise PackSelectionError(
            f"unknown domain pack {name!r}; registered packs: {sorted(_REGISTRY)}"
        )
    validate_pack(pack)
    _ACTIVE = pack
    logger.info("domain pack selected: %s@%s", pack.name, pack.version)
    return pack


def activate_configured_pack(domain_pack: str | None) -> DomainPack:
    """Startup entry point: select the pack named by CAM_DOMAIN_PACK. There is
    no implicit default (decision D-5) — unset → refuse to serve."""
    if not domain_pack:
        raise PackSelectionError(
            "CAM_DOMAIN_PACK is not set; no implicit default domain pack "
            "(refuse to serve)"
        )
    register_builtin_packs()
    return select_pack(domain_pack)


def load_pack_plugins() -> None:
    """Discover packs published as `cam.packs` entry points. A broken plugin is
    logged (PII-free) and skipped — it never crashes discovery. In-repo packs
    remain the only supported v1 distribution channel (decision D-7)."""
    import importlib.metadata as md

    try:
        eps = md.entry_points(group="cam.packs")
    except TypeError:  # pragma: no cover - older importlib API
        eps = md.entry_points().get("cam.packs", [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            obj = ep.load()
            pack = obj() if callable(obj) and not isinstance(obj, DomainPack) else obj
            if isinstance(pack, DomainPack):
                register_pack(pack)
        except Exception as exc:  # noqa: BLE001 - skip a bad plugin, never crash
            logger.warning("skipping domain-pack plugin %r: %s", ep.name, exc)


def register_builtin_packs() -> None:
    """Register the in-repo reference packs (idempotent)."""
    from cam.packs.consulting.pack import CONSULTING_PACK
    from cam.packs.immigration.pack import IMMIGRATION_PACK

    for pack in (IMMIGRATION_PACK, CONSULTING_PACK):
        _REGISTRY.setdefault(pack.name, pack)


def reset_registry() -> None:
    """Test helper — clear registry and active selection."""
    global _ACTIVE
    _REGISTRY.clear()
    _ACTIVE = None
