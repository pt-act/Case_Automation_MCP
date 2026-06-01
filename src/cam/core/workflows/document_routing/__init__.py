"""Document routing — classify, name, file, ACL, privilege-aware hard gate."""

from cam.core.workflows.document_routing.config import (
    DOCUMENT_CLASSES,
    FolderMap,
    RoutingConfig,
    get_routing_config,
    set_routing_config,
)
from cam.core.workflows.document_routing.privilege_gate import PrivilegeGate
from cam.core.workflows.document_routing.service import RoutingDecisionStore, RoutingService
from cam.core.workflows.document_routing.types import (
    GateVerdict,
    RouteOptions,
    RouteRequest,
    RouteResult,
    RoutingDecision,
    RoutingDestination,
)

__all__ = [
    "DOCUMENT_CLASSES",
    "FolderMap",
    "GateVerdict",
    "PrivilegeGate",
    "RouteOptions",
    "RouteRequest",
    "RouteResult",
    "RoutingConfig",
    "RoutingDecision",
    "RoutingDecisionStore",
    "RoutingDestination",
    "RoutingService",
    "get_routing_config",
    "set_routing_config",
]
