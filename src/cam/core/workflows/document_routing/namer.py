"""Namer + Resolver — derive_name + routing destination — spec G3."""

from __future__ import annotations

from datetime import UTC, datetime

from cam.core.domain.models import Document, Matter
from cam.core.workflows.document_routing.config import (
    RoutingConfig,
    get_routing_config,
)
from cam.core.workflows.document_routing.types import RoutingDestination


class Namer:
    """Derives a canonical, filesystem-safe document name."""

    def derive_name(
        self,
        document: Document,
        class_name: str,
        matter: Matter,
        config: RoutingConfig | None = None,
    ) -> str:
        """Return a deterministic, sanitised document name."""
        cfg = config or get_routing_config()
        date_str = datetime.now(tz=UTC).strftime("%Y%m%d")
        return cfg.naming_template.render(
            matter_reference=matter.reference,
            class_name=class_name,
            date=date_str,
            version=document.version,
        )


class Resolver:
    """Resolves (document, class) → RoutingDestination.

    Totality: every class (including 'unknown') maps to either a concrete folder
    or review_queue.  Unresolved matter → review_queue.  force_review overrides.
    """

    def resolve(
        self,
        document: Document,
        class_name: str,
        matter: Matter | None,
        force_review: bool = False,
        config: RoutingConfig | None = None,
    ) -> RoutingDestination:
        cfg = config or get_routing_config()

        if force_review:
            return RoutingDestination(
                kind="review_queue",
                matter_id=matter.id if matter else None,
                reason="force_review=True",
            )

        if matter is None:
            return RoutingDestination(
                kind="review_queue",
                reason="Matter could not be resolved.",
            )

        if class_name == "unknown":
            return RoutingDestination(
                kind="review_queue",
                matter_id=matter.id,
                reason="Document class could not be determined.",
            )

        folder = cfg.folder_map.folder_for(class_name)
        if folder is None:
            # Should not happen if totality is enforced at load; safe fallback
            return RoutingDestination(
                kind="review_queue",
                matter_id=matter.id,
                reason=f"No folder mapping for class {class_name!r}.",
            )

        # Prefix with matter reference for matter-scoped filing
        full_folder = f"{matter.reference}/{folder}"
        return RoutingDestination(
            kind="folder",
            matter_id=matter.id,
            folder=full_folder,
        )
