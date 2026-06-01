"""Intake config loading — mapping schema, case-type configs, dedupe config — spec G1.2.

All values are ASSUMPTION (confirm) until the firm confirms exact field sets,
case types, and dedupe thresholds.  Config is data-driven (CONVENTIONS §8).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cam.core.workflows.intake.types import CaseTypeConfig, TaskTemplate

# ---------------------------------------------------------------------------
# Default case-type configs — ASSUMPTION (confirm) for all values
# ---------------------------------------------------------------------------

DEFAULT_CASE_TYPE_CONFIGS: dict[str, CaseTypeConfig] = {
    "family-based": CaseTypeConfig(
        case_type="family-based",
        required_fields=["full_name", "dob", "country_of_origin", "current_status", "email"],
        opening_tasks=[
            TaskTemplate(title="Collect I-130 petition documents", assignee_role="paralegal", sort=1),
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
    default_case_type: str = "other/uncategorised"
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
