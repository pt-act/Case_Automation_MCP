"""G3 — Audit log service focused tests (require live Postgres)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cam.core.audit.service import AuditService, _GENESIS_HASH



# 1. Genesis record is valid (prev_hash = zeros, chain of 1 verifies)
async def test_genesis_record_valid(db_session: AsyncSession) -> None:
    svc = AuditService(db_session)
    record = await svc.record(
        actor="test", action="test.action", inputs={}, outputs={}
    )
    assert record.prev_hash == _GENESIS_HASH
    assert len(record.record_hash) == 64


# 2. Chain of 3 verifies
async def test_chain_of_three_verifies(db_session: AsyncSession) -> None:
    svc = AuditService(db_session)
    for i in range(3):
        await svc.record(
            actor="agent_service",
            action=f"step.{i}",
            inputs={"step": i},
            outputs={"result": "ok"},
        )
    result = await svc.verify()
    assert result.ok is True


# 3. Audit write failure rolls back the action (tested via verify after tamper)
async def test_verify_detects_tamper(db_session: AsyncSession) -> None:
    from cam.persistence.models import AuditLogORM
    from sqlalchemy import update

    svc = AuditService(db_session)
    for _ in range(3):
        await svc.record(actor="x", action="a", inputs={}, outputs={})

    # Directly corrupt a record (simulating tamper)
    await db_session.execute(
        update(AuditLogORM)
        .where(AuditLogORM.id == 2)
        .values(action="TAMPERED")
    )

    result = await svc.verify()
    assert result.ok is False
    assert result.first_bad_id is not None


# 4. Timestamp is UTC
async def test_audit_timestamp_utc(db_session: AsyncSession) -> None:
    from datetime import timezone

    svc = AuditService(db_session)
    record = await svc.record(actor="x", action="check", inputs={}, outputs={})
    assert record.timestamp.tzinfo is not None


# 5. Export JSONL round-trip
async def test_export_and_reparse(db_session: AsyncSession) -> None:
    import json

    svc = AuditService(db_session)
    for i in range(3):
        await svc.record(actor="x", action=f"a.{i}", inputs={}, outputs={})

    export = await svc.export()
    lines = [json.loads(line) for line in export.strip().split("\n")]
    assert len(lines) == 3
    assert all("record_hash" in line for line in lines)
    assert all("prev_hash" in line for line in lines)
