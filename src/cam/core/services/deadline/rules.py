"""Rule store — content-hash versioning, append-only, fail-closed validation — spec §4.4, G1.

rule_version = sha256(canonical_yaml_bytes).hexdigest()[:16]
Identical content → identical version (idempotent load).
Changed content → new version, old retained (append-only).
A bad rule never silently loads alongside good ones without surfacing the rejection.
"""

from __future__ import annotations

import hashlib
from typing import Any

import structlog

from cam.core.services.deadline.types import (
    DeadlineRule,
    EscalationLevel,
    EscalationPolicy,
    Offset,
    ReminderOffset,
    RuleRef,
)

log = structlog.get_logger(__name__)

SUPPORTED_TRIGGERS = {"trigger_date", "filing_date", "incident_date", "received_date",
                      "receipt_date", "biometrics_date", "interview_date"}
SUPPORTED_CALENDARS = {"us_federal"}


class RuleValidationError(Exception):
    """A rule could not be parsed or failed validation — logged, not silently dropped."""

    def __init__(self, rule_id: str | None, reason: str) -> None:
        self.rule_id = rule_id
        self.reason = reason
        super().__init__(f"Rule validation failed [{rule_id}]: {reason}")


# ---------------------------------------------------------------------------
# Rule store
# ---------------------------------------------------------------------------


class RuleStore:
    """In-memory append-only rule registry with content-hash versioning.

    Production would persist to the `rule_version_registry` table (migration 003).
    The interface is the same; tests use this in-memory version.
    """

    def __init__(self) -> None:
        # rule_id → list of (version, rule) ordered by load time
        self._registry: dict[str, list[tuple[str, DeadlineRule]]] = {}

    def load(self, yaml_content: str, *, audit_fn: Any | None = None) -> DeadlineRule:
        """Parse + validate a YAML rule and append it to the registry.

        Raises RuleValidationError if the rule is invalid — never partially loads.
        """
        rule = _parse_and_validate(yaml_content)
        version = _content_hash(yaml_content)
        RuleRef(
            rule_id=rule.rule_id,
            rule_version=version,
            jurisdiction=rule.jurisdiction,
            practice_area=rule.practice_area,
        )

        existing = self._registry.setdefault(rule.rule_id, [])
        if any(v == version for v, _ in existing):
            log.debug("rule_store.idempotent_load", rule_id=rule.rule_id, version=version)
            return rule

        existing.append((version, rule))
        log.info("rule_store.loaded", rule_id=rule.rule_id, version=version)
        return rule

    def register_rule(self, rule: DeadlineRule, *, version: str | None = None) -> DeadlineRule:
        """Append an already-parsed `DeadlineRule` (e.g. from the active domain
        pack) without going through YAML. Content-hash versioned and idempotent,
        mirroring `load()`.  Used by `cam.packs.build_rule_store_from_pack`."""
        version = version or _content_hash(rule.model_dump_json())
        existing = self._registry.setdefault(rule.rule_id, [])
        if any(v == version for v, _ in existing):
            return rule
        existing.append((version, rule))
        log.info("rule_store.registered", rule_id=rule.rule_id, version=version)
        return rule

    def active(self, rule_id: str, jurisdiction: str = "US") -> DeadlineRule:
        """Return the most recently loaded version of a rule."""
        entries = self._registry.get(rule_id, [])
        matching = [(v, r) for v, r in entries if r.jurisdiction == jurisdiction]
        if not matching:
            raise KeyError(f"No active rule for {rule_id!r} / {jurisdiction!r}.")
        return matching[-1][1]

    def versions(self, rule_id: str) -> list[RuleRef]:
        entries = self._registry.get(rule_id, [])
        return [
            RuleRef(
                rule_id=rule_id,
                rule_version=v,
                jurisdiction=r.jurisdiction,
                practice_area=r.practice_area,
            )
            for v, r in entries
        ]

    def rule_version(self, rule_id: str, jurisdiction: str = "US") -> str:
        self.active(rule_id, jurisdiction)
        entries = self._registry.get(rule_id, [])
        for v, r in reversed(entries):
            if r.jurisdiction == jurisdiction:
                return v
        raise KeyError(rule_id)

    def all_rules(self, jurisdiction: str = "US") -> list[tuple[str, str, DeadlineRule]]:
        """Return (rule_id, version, rule) for the active version of each rule."""
        result = []
        for rule_id, entries in self._registry.items():
            matching = [(v, r) for v, r in entries if r.jurisdiction == jurisdiction]
            if matching:
                v, r = matching[-1]
                result.append((rule_id, v, r))
        return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _content_hash(yaml_content: str) -> str:
    return hashlib.sha256(yaml_content.encode()).hexdigest()[:16]


def _parse_and_validate(yaml_content: str) -> DeadlineRule:
    """Parse YAML → DeadlineRule; raise RuleValidationError on any issue."""
    try:
        import yaml as _yaml
        raw: dict = _yaml.safe_load(yaml_content)
    except Exception as exc:
        raise RuleValidationError(None, f"YAML parse error: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuleValidationError(None, "Rule must be a YAML mapping.")

    rule_id = raw.get("rule_id")
    if not rule_id:
        raise RuleValidationError(None, "rule_id is required.")

    trigger = raw.get("trigger")
    if not trigger:
        raise RuleValidationError(rule_id, "trigger field is required.")

    calendar_id = raw.get("calendar_id", "us_federal")
    if calendar_id not in SUPPORTED_CALENDARS:
        raise RuleValidationError(rule_id, f"Unknown calendar_id {calendar_id!r}.")

    try:
        offset_raw = raw.get("offset", {})
        offset = Offset(**offset_raw) if isinstance(offset_raw, dict) else Offset()
    except Exception as exc:
        raise RuleValidationError(rule_id, f"Invalid offset: {exc}") from exc

    reminders: list[ReminderOffset] = []
    for r in raw.get("reminders", []):
        try:
            reminders.append(ReminderOffset(**r) if isinstance(r,
                dict) else ReminderOffset(amount=r, unit="days"))
        except Exception as exc:
            raise RuleValidationError(rule_id, f"Invalid reminder: {exc}") from exc

    try:
        esc_raw = raw.get("escalation", {})
        levels = []
        for lvl in esc_raw.get("levels", []):
            levels.append(EscalationLevel(**lvl))
        escalation = EscalationPolicy(levels=levels)
    except Exception as exc:
        raise RuleValidationError(rule_id, f"Invalid escalation: {exc}") from exc

    return DeadlineRule(
        rule_id=rule_id,
        jurisdiction=raw.get("jurisdiction", "US"),
        practice_area=raw.get("practice_area"),
        trigger=trigger,
        offset=offset,
        adjust=raw.get("adjust", "next_business_day"),
        calendar_id=calendar_id,
        reminders=reminders,
        escalation=escalation,
        description=raw.get("description", ""),
        assumption_unconfirmed=raw.get("assumption_unconfirmed", True),
    )
