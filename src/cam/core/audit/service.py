"""Append-only, hash-chained audit log service — spec §4.3, §5.2–§5.3.

Hash function:  SHA-256
Canonical serialisation:  sorted-key JSON (version-tagged v1; must not change
after go-live — see ADR 001).

Genesis sentinel:  prev_hash = "0" * 64  (64 hex zeros).

Write-before-complete:  `record()` must be called inside the same
`unit_of_work()` context as the action it audits.  The single transaction
guarantees that the audit write and the action commit together or neither
does (NFR-2).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cam.core.domain.models import AuditRecord
from cam.persistence.models import AuditLogORM
from cam.persistence.repositories import AuditRepository

_GENESIS_HASH = "0" * 64
_CANONICAL_VERSION = "v1"


def _canonical(record: dict[str, Any]) -> str:
    """Sorted-key JSON with no whitespace — the stable serialisation for hashing."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)


def _compute_hash(prev_hash: str, record_without_hash: dict[str, Any]) -> str:
    material = prev_hash + _canonical(record_without_hash)
    return hashlib.sha256(material.encode()).hexdigest()


class VerifyResult:
    def __init__(self, ok: bool, first_bad_id: int | None = None) -> None:
        self.ok = ok
        self.first_bad_id = first_bad_id

    def __bool__(self) -> bool:
        return self.ok


class AuditService:
    """Service for appending to and verifying the hash-chained audit log."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = AuditRepository(session)
        self._session = session

    async def record(
        self,
        actor: str,
        action: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        run_id: str | None = None,
        approval: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Append one audit record.  Must be called within an active `unit_of_work()`.

        The record_hash is computed over all fields *except* itself, then stored.
        The previous record's hash is fetched inside this transaction (serialisable
        read) to maintain chain order.
        """
        latest = await self._repo.get_latest()
        prev_hash = latest.record_hash if latest else _GENESIS_HASH

        now = datetime.now(tz=timezone.utc)

        record_body: dict[str, Any] = {
            "canonical_version": _CANONICAL_VERSION,
            "actor": actor,
            "action": action,
            "inputs": inputs,
            "outputs": outputs,
            "approval": approval,
            "timestamp": now.isoformat(),
            "run_id": run_id,
            "prev_hash": prev_hash,
        }
        record_hash = _compute_hash(prev_hash, record_body)

        orm = AuditLogORM(
            actor=actor,
            action=action,
            inputs=inputs,
            outputs=outputs,
            approval=approval,
            timestamp=now,
            run_id=run_id,
            prev_hash=prev_hash,
            record_hash=record_hash,
        )
        saved = await self._repo.append(orm)

        return AuditRecord(
            id=saved.id,
            actor=actor,
            action=action,
            inputs=inputs,
            outputs=outputs,
            approval=approval,
            timestamp=now,
            run_id=run_id,
            prev_hash=prev_hash,
            record_hash=record_hash,
        )

    async def verify(
        self,
        start_id: int | None = None,
        end_id: int | None = None,
    ) -> VerifyResult:
        """Recompute the chain and return the first broken link if any.

        Walks records in monotonic id order, recomputing each hash from the
        stored prev_hash and the record body.  Returns VerifyResult(ok=False,
        first_bad_id=N) if a mismatch is found.
        """
        rows = await self._repo.get_range(start_id, end_id)
        if not rows:
            return VerifyResult(ok=True)

        expected_prev = _GENESIS_HASH

        for row in rows:
            record_body: dict[str, Any] = {
                "canonical_version": _CANONICAL_VERSION,
                "actor": row.actor,
                "action": row.action,
                "inputs": row.inputs,
                "outputs": row.outputs,
                "approval": row.approval,
                "timestamp": row.timestamp.isoformat(),
                "run_id": row.run_id,
                "prev_hash": row.prev_hash,
            }
            expected_hash = _compute_hash(row.prev_hash, record_body)

            if row.prev_hash != expected_prev:
                return VerifyResult(ok=False, first_bad_id=row.id)
            if row.record_hash != expected_hash:
                return VerifyResult(ok=False, first_bad_id=row.id)

            expected_prev = row.record_hash

        return VerifyResult(ok=True)

    async def export(
        self,
        start_id: int | None = None,
        end_id: int | None = None,
        fmt: str = "jsonl",
    ) -> str:
        """Export audit records as JSONL (one JSON object per line).

        The exported JSONL includes stored hashes so the chain can be
        re-verified after import.  Access is RBAC-scoped by the caller
        (caller must hold Permission.AUDIT_EXPORT).
        """
        rows = await self._repo.get_range(start_id, end_id)
        lines: list[str] = []
        for row in rows:
            entry = {
                "id": row.id,
                "actor": row.actor,
                "action": row.action,
                "inputs": row.inputs,
                "outputs": row.outputs,
                "approval": row.approval,
                "timestamp": row.timestamp.isoformat(),
                "run_id": row.run_id,
                "prev_hash": row.prev_hash,
                "record_hash": row.record_hash,
            }
            lines.append(json.dumps(entry, separators=(",", ":")))
        return "\n".join(lines)
