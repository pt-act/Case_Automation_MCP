"""In-memory reference DocStoreConnector — G6.1d."""

from __future__ import annotations

import hashlib
import uuid

from cam.connectors.errors import ConnectorError, NotFoundError
from cam.core.domain.models import ACL, Document


class ReferenceDocStoreConnector:
    """Deterministic in-memory DocStoreConnector.

    Checksums are computed from stored bytes so put→get round-trips are stable.
    """

    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}
        self._blobs: dict[str, bytes] = {}
        self._inject_error: ConnectorError | None = None

    def inject_error(self, error: ConnectorError) -> None:
        self._inject_error = error

    def _maybe_raise(self) -> None:
        if self._inject_error is not None:
            err = self._inject_error
            self._inject_error = None
            raise err

    async def put(self, doc: Document, content: bytes) -> Document:
        self._maybe_raise()
        checksum = hashlib.sha256(content).hexdigest()
        stored_doc = doc.model_copy(
            update={
                "id": doc.id or str(uuid.uuid4()),
                "checksum": checksum,
                "uri": f"reference://docs/{doc.id or 'new'}",
            }
        )
        self._docs[stored_doc.id] = stored_doc
        self._blobs[stored_doc.id] = content
        return stored_doc

    async def get(self, id: str) -> tuple[Document, bytes]:
        self._maybe_raise()
        doc = self._docs.get(id)
        blob = self._blobs.get(id)
        if doc is None or blob is None:
            raise NotFoundError("reference_docstore", f"Document {id!r} not found.")
        return doc, blob

    async def move(self, id: str, folder: str, acl: ACL) -> Document:
        self._maybe_raise()
        doc = self._docs.get(id)
        if doc is None:
            raise NotFoundError("reference_docstore", f"Document {id!r} not found.")
        moved = doc.model_copy(update={"uri": f"reference://docs/{folder}/{id}"})
        self._docs[id] = moved
        return moved
