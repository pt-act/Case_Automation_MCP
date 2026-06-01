"""In-memory reference EmailConnector — G6.1c."""

from __future__ import annotations

import uuid

from cam.connectors.errors import ConnectorError, NotFoundError
from cam.core.domain.models import Communication


class ReferenceEmailConnector:
    """Deterministic in-memory EmailConnector.

    Draft IDs are allocated on create_draft; idempotency on `send` is enforced
    by tracking sent idem_keys so a repeated send returns the original message id.
    """

    def __init__(self) -> None:
        self._drafts: dict[str, Communication] = {}
        self._sent: dict[str, str] = {}        # idem_key → message_id
        self._inject_error: ConnectorError | None = None

    def inject_error(self, error: ConnectorError) -> None:
        self._inject_error = error

    def _maybe_raise(self) -> None:
        if self._inject_error is not None:
            err = self._inject_error
            self._inject_error = None
            raise err

    async def create_draft(self, c: Communication) -> str:
        self._maybe_raise()
        draft_id = str(uuid.uuid4())
        self._drafts[draft_id] = c.model_copy(update={"id": draft_id, "status": "draft"})
        return draft_id

    async def send(self, draft_id: str, idem_key: str) -> str:
        self._maybe_raise()
        # Idempotency: same idem_key → same message_id, no second send
        if idem_key in self._sent:
            return self._sent[idem_key]
        if draft_id not in self._drafts:
            raise NotFoundError("reference_email", f"Draft {draft_id!r} not found.")
        message_id = str(uuid.uuid4())
        self._drafts[draft_id] = self._drafts[draft_id].model_copy(update={"status": "sent"})
        self._sent[idem_key] = message_id
        return message_id

    # Test helpers
    def get_draft(self, draft_id: str) -> Communication | None:
        return self._drafts.get(draft_id)

    def was_sent(self, idem_key: str) -> bool:
        return idem_key in self._sent
