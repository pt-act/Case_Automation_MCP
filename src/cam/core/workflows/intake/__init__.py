"""Client Intake workflow — lead → matter + contact + tasks + gated welcome."""

from cam.core.workflows.intake.config import IntakeConfig, get_intake_config, set_intake_config
from cam.core.workflows.intake.types import (
    CaseTypeConfig,
    DedupeResult,
    IntakeFields,
    IntakeGap,
    LeadPayload,
    MatchRef,
    TaskTemplate,
)
from cam.core.workflows.intake.workflow import (
    IntakeServices,
    register_intake_workflow,
    tool_intake_run,
)

__all__ = [
    "CaseTypeConfig",
    "DedupeResult",
    "IntakeConfig",
    "IntakeFields",
    "IntakeGap",
    "IntakeServices",
    "LeadPayload",
    "MatchRef",
    "TaskTemplate",
    "get_intake_config",
    "register_intake_workflow",
    "set_intake_config",
    "tool_intake_run",
]
