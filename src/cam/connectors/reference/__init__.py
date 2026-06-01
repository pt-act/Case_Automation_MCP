"""In-memory reference adapters for all four connector categories."""

from cam.connectors.reference.case import ReferenceCaseConnector
from cam.connectors.reference.crm import ReferenceCRMConnector
from cam.connectors.reference.docstore import ReferenceDocStoreConnector
from cam.connectors.reference.email import ReferenceEmailConnector

__all__ = [
    "ReferenceCaseConnector",
    "ReferenceCRMConnector",
    "ReferenceDocStoreConnector",
    "ReferenceEmailConnector",
]


def register_reference_adapters() -> None:
    """Register all four reference adapters under the name 'reference'."""
    from cam.connectors.registry import register_connector
    from cam.connectors.webhook.pipeline import reference_normaliser

    register_connector(
        "reference",
        case=ReferenceCaseConnector(),
        crm=ReferenceCRMConnector(),
        email=ReferenceEmailConnector(),
        docstore=ReferenceDocStoreConnector(),
        normaliser=reference_normaliser,
    )
