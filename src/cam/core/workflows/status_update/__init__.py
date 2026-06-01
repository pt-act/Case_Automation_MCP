"""Status-update-emails workflow — status change → tailored draft → gate → send."""

from cam.core.workflows.status_update.change_id import derive_change_id, sweep_change_id, webhook_change_id
from cam.core.workflows.status_update.store import LastKnownStatusStore, StatusUpdateConfig
from cam.core.workflows.status_update.types import MatterStatusDelta, StatusUpdateRunState
from cam.core.workflows.status_update.workflow import (
    StatusUpdateServices,
    configure_status_update_services,
    get_services,
    handle_status_change_event,
    register_status_update_workflow,
    sweep_matters,
)

__all__ = [
    "LastKnownStatusStore",
    "MatterStatusDelta",
    "StatusUpdateConfig",
    "StatusUpdateRunState",
    "StatusUpdateServices",
    "configure_status_update_services",
    "derive_change_id",
    "get_services",
    "handle_status_change_event",
    "register_status_update_workflow",
    "sweep_change_id",
    "sweep_matters",
    "webhook_change_id",
]
