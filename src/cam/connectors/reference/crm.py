"""In-memory reference CRMConnector — G6.1b."""

from __future__ import annotations

import uuid

from cam.connectors.errors import ConnectorError
from cam.core.domain.models import Contact


class ReferenceCRMConnector:
    """Deterministic in-memory CRMConnector."""

    def __init__(self) -> None:
        self._contacts: dict[str, Contact] = {}
        self._inject_error: ConnectorError | None = None

    def inject_error(self, error: ConnectorError) -> None:
        self._inject_error = error

    def _maybe_raise(self) -> None:
        if self._inject_error is not None:
            err = self._inject_error
            self._inject_error = None
            raise err

    async def upsert_contact(self, c: Contact) -> Contact:
        self._maybe_raise()
        if not c.id:
            c = c.model_copy(update={"id": str(uuid.uuid4())})
        self._contacts[c.id] = c
        return c

    async def find_contact(self, q: str) -> list[Contact]:
        self._maybe_raise()
        q_lower = q.lower()
        return [
            c for c in self._contacts.values()
            if q_lower in (c.name or "").lower()
            or q_lower in (str(c.email) if c.email else "").lower()
        ]
