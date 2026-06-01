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
    configure_services,
    get_services,
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
    "configure_services",
    "get_intake_config",
    "get_services",
    "register_intake_workflow",
    "set_intake_config",
    "tool_intake_run",
]
