"""SQLAlchemy 2 typed ORM models mirroring the six domain types + audit_log.

Domain ⇄ ORM mapping is internal to this package; callers never see ORM
objects — only domain models (spec §4.2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Contact
# ---------------------------------------------------------------------------


class ContactORM(Base):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    matters: Mapped[list[MatterORM]] = relationship(
        "MatterORM", back_populates="client_rel", foreign_keys="MatterORM.client_id"
    )


# ---------------------------------------------------------------------------
# Matter
# ---------------------------------------------------------------------------


class MatterORM(Base):
    __tablename__ = "matters"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    reference: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    practice_area: Mapped[str | None] = mapped_column(String, nullable=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    responsible: Mapped[str | None] = mapped_column(String, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    client_rel: Mapped[ContactORM] = relationship(
        "ContactORM", back_populates="matters", foreign_keys=[client_id]
    )
    deadlines: Mapped[list[DeadlineORM]] = relationship(
        "DeadlineORM", back_populates="matter_rel"
    )
    documents: Mapped[list[DocumentORM]] = relationship(
        "DocumentORM", back_populates="matter_rel"
    )
    tasks: Mapped[list[TaskORM]] = relationship(
        "TaskORM", back_populates="matter_rel"
    )
    communications: Mapped[list[CommunicationORM]] = relationship(
        "CommunicationORM", back_populates="matter_rel"
    )


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


class DocumentORM(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    matter_id: Mapped[str] = mapped_column(ForeignKey("matters.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    uri: Mapped[str] = mapped_column(String, nullable=False)
    classification: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    privileged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    matter_rel: Mapped[MatterORM] = relationship("MatterORM", back_populates="documents")


# ---------------------------------------------------------------------------
# Deadline
# ---------------------------------------------------------------------------


class DeadlineORM(Base):
    __tablename__ = "deadlines"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    matter_id: Mapped[str] = mapped_column(ForeignKey("matters.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    matter_rel: Mapped[MatterORM] = relationship("MatterORM", back_populates="deadlines")


# ---------------------------------------------------------------------------
# Communication
# ---------------------------------------------------------------------------


class CommunicationORM(Base):
    __tablename__ = "communications"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    matter_id: Mapped[str | None] = mapped_column(
        ForeignKey("matters.id"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    participants: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list[Any])

    matter_rel: Mapped[MatterORM | None] = relationship(
        "MatterORM", back_populates="communications"
    )


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class TaskORM(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    matter_id: Mapped[str] = mapped_column(ForeignKey("matters.id"), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    assignee: Mapped[str | None] = mapped_column(String, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)

    matter_rel: Mapped[MatterORM] = relationship("MatterORM", back_populates="tasks")


# ---------------------------------------------------------------------------
# Audit log — append-only, hash-chained
# ---------------------------------------------------------------------------


class AuditLogORM(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    approval: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)


# ---------------------------------------------------------------------------
# DB-level immutability guard for audit_log
# ---------------------------------------------------------------------------


_IMMUTABILITY_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION audit_log_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log rows are immutable; UPDATE and DELETE are prohibited.';
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;

CREATE TRIGGER trg_audit_log_immutable
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
"""

_REVOKE_MUTATION_SQL = """
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;
"""
