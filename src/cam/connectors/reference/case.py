"""In-memory reference CaseConnector — G6.1a.

Deterministic in-memory store implementing the CaseConnector port.
Used for testing, local development, and contract-test harness validation.
Registered under name "reference".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from cam.connectors.errors import ConnectorError, NotFoundError
from cam.connectors.ports import MatterDraft
from cam.core.domain.models import Contact, Deadline, Matter


class ReferenceCaseConnector:
    """Deterministic in-memory CaseConnector.

    Supports fault injection for testing degradation paths.
    """

    def __init__(self) -> None:
        self._matters: dict[str, Matter] = {}
        self._deadlines: dict[str, list[Deadline]] = {}
        self._inject_error: ConnectorError | None = None

    # ------------------------------------------------------------------
    # Fault injection (test helper)
    # ------------------------------------------------------------------

    def inject_error(self, error: ConnectorError) -> None:
        """Force the next call to raise the given error."""
        self._inject_error = error

    def _maybe_raise(self) -> None:
        if self._inject_error is not None:
            err = self._inject_error
            self._inject_error = None
            raise err

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------

    async def get_matter(self, id: str) -> Matter:
        self._maybe_raise()
        matter = self._matters.get(id)
        if matter is None:
            raise NotFoundError("reference_case", f"Matter {id!r} not found.")
        return matter

    async def create_matter(self, data: MatterDraft) -> Matter:
        self._maybe_raise()
        matter_id = str(uuid.uuid4())
        matter = Matter(
            id=matter_id,
            source="reference",
            reference=data.reference,
            title=data.title,
            status=data.status,
            practice_area=data.practice_area,
            client=data.client,
            responsible=data.responsible,
            opened_at=data.opened_at,
            external_ids={**data.external_ids, "reference": matter_id},
        )
        self._matters[matter_id] = matter
        self._deadlines[matter_id] = []
        return matter

    async def list_deadlines(self, matter_id: str) -> list[Deadline]:
        self._maybe_raise()
        if matter_id not in self._matters:
            raise NotFoundError("reference_case", f"Matter {matter_id!r} not found.")
        return list(self._deadlines.get(matter_id, []))

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def seed_matter(self, matter: Matter) -> None:
        self._matters[matter.id] = matter
        if matter.id not in self._deadlines:
            self._deadlines[matter.id] = []

    def seed_deadline(self, deadline: Deadline) -> None:
        self._deadlines.setdefault(deadline.matter_id, []).append(deadline)
