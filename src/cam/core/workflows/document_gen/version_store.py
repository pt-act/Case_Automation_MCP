"""Versioning, idempotency, DocStoreConnector.put — spec G4.

Version is monotonic per (matter_id, template_name, doc_key).
Idempotency key prevents double-store under retry.
Collision with differing content refused with an error.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

from cam.core.domain.models import Document

log = structlog.get_logger(__name__)


class VersionCollisionError(Exception):
    """Raised when an idempotency key collision occurs with differing content."""


class InMemoryVersionLedger:
    """In-memory version ledger and idempotency store for tests.

    Production uses a DB sequence + unique constraint (migration 004).
    """

    def __init__(self) -> None:
        self._versions: dict[tuple, int] = {}          # (matter_id, template_name, doc_key) → latest
        self._idem: dict[str, Document] = {}            # idem_key → stored Document
        self._idem_checksums: dict[str, str] = {}       # idem_key → checksum at store time

    def next_version(self, matter_id: str, template_name: str, doc_key: str) -> int:
        key = (matter_id, template_name, doc_key)
        n = self._versions.get(key, 0) + 1
        self._versions[key] = n
        return n

    def lookup_idem(self, idem_key: str) -> Document | None:
        return self._idem.get(idem_key)

    def record_idem(self, idem_key: str, doc: Document, rendered_checksum: str) -> None:
        existing_checksum = self._idem_checksums.get(idem_key)
        if existing_checksum is not None and existing_checksum != rendered_checksum:
            raise VersionCollisionError(
                f"Idempotency key {idem_key!r} collision: "
                f"stored checksum differs from new content."
            )
        self._idem[idem_key] = doc
        self._idem_checksums[idem_key] = rendered_checksum


def derive_idem_key(
    template_name: str,
    template_version: int,
    context: dict[str, Any],
) -> str:
    """Derive a stable idempotency key from generation inputs."""
    canonical = json.dumps(
        {"template": template_name, "version": template_version, "context": context},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


async def store_document(
    *,
    matter_id: str,
    template_name: str,
    template_version: int,
    doc_key: str,
    rendered_bytes: bytes,
    rendered_checksum: str,
    privileged: bool,
    classification: str | None,
    idem_key: str,
    ledger: InMemoryVersionLedger,
    docstore: Any,  # DocStoreConnector port
) -> Document:
    """Allocate a version and persist via DocStoreConnector.

    Idempotent: if idem_key already stored, return existing Document.
    Refuses collision (same key, different checksum).
    """
    existing = ledger.lookup_idem(idem_key)
    if existing is not None:
        return existing

    from datetime import datetime, timezone
    import uuid

    version = ledger.next_version(matter_id, template_name, doc_key)
    doc = Document(
        id=str(uuid.uuid4()),
        matter_id=matter_id,
        name=f"{template_name}_v{version}.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        uri="",  # filled by docstore
        classification=classification,
        version=version,
        privileged=privileged,
        checksum=rendered_checksum,
        created_at=datetime.now(tz=timezone.utc),
    )
    stored = await docstore.put(doc, rendered_bytes)
    ledger.record_idem(idem_key, stored, rendered_checksum)
    log.info(
        "docgen.stored",
        template=template_name,
        version=version,
        matter_id=matter_id,
        checksum=rendered_checksum[:12],
    )
    return stored
