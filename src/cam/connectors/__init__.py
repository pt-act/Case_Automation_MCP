"""Connector framework — ports, errors, registry, middleware, webhook ingestion, reference adapters."""

from cam.connectors.errors import (
    AuthError,
    ConnectorError,
    FatalError,
    NotFoundError,
    RateLimitError,
    TransientError,
    classify,
)
from cam.connectors.health import ConnectorHealth, get_health
from cam.connectors.middleware import OutboundClient, RetryPolicy
from cam.connectors.ports import (
    CaseConnector,
    CRMConnector,
    DocStoreConnector,
    EmailConnector,
    MatterDraft,
    TriggerSink,
)
from cam.connectors.registry import (
    ConnectorNotFound,
    get_connector,
    register_connector,
)
from cam.connectors.webhook.models import Event

__all__ = [
    "AuthError",
    "CaseConnector",
    "CRMConnector",
    "ConnectorError",
    "ConnectorHealth",
    "ConnectorNotFound",
    "DocStoreConnector",
    "EmailConnector",
    "Event",
    "FatalError",
    "MatterDraft",
    "NotFoundError",
    "OutboundClient",
    "RateLimitError",
    "RetryPolicy",
    "TransientError",
    "TriggerSink",
    "classify",
    "get_connector",
    "get_health",
    "register_connector",
]
