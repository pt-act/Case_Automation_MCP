"""Intake config loading — mapping schema, case-type configs, dedupe config — spec G1.2.

Case-type configs are domain-specific and live in the **active domain pack**, not
here (G6 / CONVENTIONS §5, §8): `apply_pack()` populates the runtime config from
the pack at startup. The neutral default below carries no practice-specific value
— it only keeps the engine self-consistent when no pack has been applied (e.g. an
isolated unit test). Config is data-driven (CONVENTIONS §8).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cam.core.workflows.intake.types import CaseTypeConfig, TaskTemplate

# ---------------------------------------------------------------------------
# Neutral, domain-agnostic fallback — a single generic case type. The real
# case types come from the active pack (each practice defines its own set).
# No domain value is hard-coded here.
# ---------------------------------------------------------------------------

_NEUTRAL_CASE_TYPE = "default"

DEFAULT_CASE_TYPE_CONFIGS: dict[str, CaseTypeConfig] = {
    _NEUTRAL_CASE_TYPE: CaseTypeConfig(
        case_type=_NEUTRAL_CASE_TYPE,
        required_fields=["full_name", "email"],
        opening_tasks=[
            TaskTemplate(title="Open matter in case system", assignee_role="staff", sort=1),
        ],
        deadline_rule_ids=[],
        welcome_template_id="default_welcome",
    ),
}


@dataclass
class DedupeConfig:
    """Dedupe thresholds and key ordering.  ASSUMPTION (confirm)."""

    email_exact_threshold: float = 1.0      # email exact match → reuse
    name_dob_threshold: float = 0.90        # name+DOB fuzzy score to reuse
    a_number_threshold: float = 1.0         # A-number exact match → reuse
    ambiguity_threshold: float = 0.70       # below this → ambiguous


@dataclass
class IntakeConfig:
    """Full intake configuration bundle."""

    case_type_configs: dict[str, CaseTypeConfig] = field(
        default_factory=lambda: dict(DEFAULT_CASE_TYPE_CONFIGS)
    )
    dedupe: DedupeConfig = field(default_factory=DedupeConfig)
    default_case_type: str = _NEUTRAL_CASE_TYPE
    confidence_threshold: float = 0.80

    def get_case_type(self, case_type: str | None) -> CaseTypeConfig:
        return self.case_type_configs.get(
            case_type or self.default_case_type,
            self.case_type_configs[self.default_case_type],
        )


# Module-level singleton (overridable in tests)
_config: IntakeConfig = IntakeConfig()


def get_intake_config() -> IntakeConfig:
    return _config


def set_intake_config(config: IntakeConfig) -> None:
    global _config
    _config = config
