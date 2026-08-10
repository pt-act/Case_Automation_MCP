"""Repository pattern — domain ⇄ ORM mapping, typed generic protocol.

Callers only see domain models; ORM objects stay internal (spec §4.2).
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeVar, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cam.core.domain.models import (
    Communication,
    Contact,
    Deadline,
    Document,
    Matter,
    Task,
)
from cam.persistence.models import (
    AuditLogORM,
    CommunicationORM,
    ContactORM,
    DeadlineORM,
    DocumentORM,
    MatterORM,
    TaskORM,
)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Generic Repository protocol
# ---------------------------------------------------------------------------


class Repository(Protocol[T]):
    async def get(self, id: str) -> T | None: ...
    async def list(self, **filters: Any) -> list[T]: ...
    async def add(self, entity: T) -> T: ...
    async def update(self, entity: T) -> T: ...
    async def delete(self, id: str) -> None: ...


# ---------------------------------------------------------------------------
# Domain ⇄ ORM mapping helpers
# ---------------------------------------------------------------------------


def _contact_to_domain(orm: ContactORM) -> Contact:
    return Contact(
        id=orm.id,
        source=orm.source,
        name=orm.name,
        email=orm.email,
        phone=orm.phone,
        role=orm.role,
        external_ids=orm.external_ids or {},
    )


def _contact_from_domain(domain: Contact) -> ContactORM:
    return ContactORM(
        id=domain.id,
        source=domain.source,
        name=domain.name,
        email=str(domain.email) if domain.email else None,
        phone=domain.phone,
        role=domain.role,
        external_ids=domain.external_ids,
    )


def _deadline_to_domain(orm: DeadlineORM) -> Deadline:
    return Deadline(
        id=orm.id,
        matter_id=orm.matter_id,
        name=orm.name,
        due_at=orm.due_at,
        rule_id=orm.rule_id,
        status=cast(Literal["pending", "reminded", "done", "missed"], orm.status),
        escalation_level=orm.escalation_level,
    )


def _matter_to_domain(orm: MatterORM) -> Matter:
    client = _contact_to_domain(orm.client_rel)
    key_dates = [_deadline_to_domain(d) for d in (orm.deadlines or [])]
    return Matter(
        id=orm.id,
        source=orm.source,
        reference=orm.reference,
        title=orm.title,
        status=orm.status,
        practice_area=orm.practice_area,
        client=client,
        responsible=orm.responsible,
        opened_at=orm.opened_at,
        key_dates=key_dates,
        external_ids=orm.external_ids or {},
    )


def _document_to_domain(orm: DocumentORM) -> Document:
    return Document(
        id=orm.id,
        matter_id=orm.matter_id,
        name=orm.name,
        mime_type=orm.mime_type,
        uri=orm.uri,
        classification=orm.classification,
        version=orm.version,
        privileged=orm.privileged,
        checksum=orm.checksum,
        created_at=orm.created_at,
    )


def _communication_to_domain(orm: CommunicationORM) -> Communication:
    participants = [
        Contact.model_validate(p) for p in (orm.participants or [])
    ]
    return Communication(
        id=orm.id,
        matter_id=orm.matter_id,
        direction=cast(Literal["in", "out"], orm.direction),
        channel=orm.channel,
        subject=orm.subject,
        body=orm.body,
        status=orm.status,
        participants=participants,
    )


def _task_to_domain(orm: TaskORM) -> Task:
    return Task(
        id=orm.id,
        matter_id=orm.matter_id,
        title=orm.title,
        assignee=orm.assignee,
        due_at=orm.due_at,
        status=orm.status,
    )


# ---------------------------------------------------------------------------
# Concrete repositories
# ---------------------------------------------------------------------------


class ContactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, id: str) -> Contact | None:
        result = await self._session.get(ContactORM, id)
        return _contact_to_domain(result) if result else None

    async def list(self, **filters: Any) -> list[Contact]:
        stmt = select(ContactORM)
        results = await self._session.scalars(stmt)
        return [_contact_to_domain(r) for r in results.all()]

    async def add(self, entity: Contact) -> Contact:
        orm = _contact_from_domain(entity)
        self._session.add(orm)
        await self._session.flush()
        return entity

    async def update(self, entity: Contact) -> Contact:
        existing = await self._session.get(ContactORM, entity.id)
        if existing is None:
            raise ValueError(f"Contact {entity.id!r} not found.")
        existing.source = entity.source
        existing.name = entity.name
        existing.email = str(entity.email) if entity.email else None
        existing.phone = entity.phone
        existing.role = entity.role
        existing.external_ids = entity.external_ids
        await self._session.flush()
        return entity

    async def delete(self, id: str) -> None:
        existing = await self._session.get(ContactORM, id)
        if existing:
            await self._session.delete(existing)
            await self._session.flush()


class MatterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, id: str) -> Matter | None:
        from sqlalchemy.orm import selectinload
        stmt = (
            select(MatterORM)
            .where(MatterORM.id == id)
            .options(
                selectinload(MatterORM.client_rel),
                selectinload(MatterORM.deadlines),
            )
        )
        result = await self._session.scalar(stmt)
        return _matter_to_domain(result) if result else None

    async def list(self, **filters: Any) -> list[Matter]:
        from sqlalchemy.orm import selectinload
        stmt = (
            select(MatterORM)
            .options(
                selectinload(MatterORM.client_rel),
                selectinload(MatterORM.deadlines),
            )
        )
        results = await self._session.scalars(stmt)
        return [_matter_to_domain(r) for r in results.all()]

    async def add(self, entity: Matter) -> Matter:
        orm = MatterORM(
            id=entity.id,
            source=entity.source,
            reference=entity.reference,
            title=entity.title,
            status=entity.status,
            practice_area=entity.practice_area,
            client_id=entity.client.id,
            responsible=entity.responsible,
            opened_at=entity.opened_at,
            external_ids=entity.external_ids,
        )
        self._session.add(orm)
        await self._session.flush()
        return entity

    async def update(self, entity: Matter) -> Matter:
        existing = await self._session.get(MatterORM, entity.id)
        if existing is None:
            raise ValueError(f"Matter {entity.id!r} not found.")
        existing.status = entity.status
        existing.practice_area = entity.practice_area
        existing.responsible = entity.responsible
        existing.external_ids = entity.external_ids
        await self._session.flush()
        return entity

    async def delete(self, id: str) -> None:
        existing = await self._session.get(MatterORM, id)
        if existing:
            await self._session.delete(existing)
            await self._session.flush()


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, id: str) -> Document | None:
        result = await self._session.get(DocumentORM, id)
        return _document_to_domain(result) if result else None

    async def list(self, **filters: Any) -> list[Document]:
        stmt = select(DocumentORM)
        results = await self._session.scalars(stmt)
        return [_document_to_domain(r) for r in results.all()]

    async def add(self, entity: Document) -> Document:
        orm = DocumentORM(
            id=entity.id,
            matter_id=entity.matter_id,
            name=entity.name,
            mime_type=entity.mime_type,
            uri=entity.uri,
            classification=entity.classification,
            version=entity.version,
            privileged=entity.privileged,
            checksum=entity.checksum,
            created_at=entity.created_at,
        )
        self._session.add(orm)
        await self._session.flush()
        return entity

    async def update(self, entity: Document) -> Document:
        existing = await self._session.get(DocumentORM, entity.id)
        if existing is None:
            raise ValueError(f"Document {entity.id!r} not found.")
        existing.classification = entity.classification
        existing.privileged = entity.privileged
        existing.checksum = entity.checksum
        existing.version = entity.version
        await self._session.flush()
        return entity

    async def delete(self, id: str) -> None:
        existing = await self._session.get(DocumentORM, id)
        if existing:
            await self._session.delete(existing)
            await self._session.flush()


class DeadlineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, id: str) -> Deadline | None:
        result = await self._session.get(DeadlineORM, id)
        return _deadline_to_domain(result) if result else None

    async def list(self, **filters: Any) -> list[Deadline]:
        stmt = select(DeadlineORM)
        results = await self._session.scalars(stmt)
        return [_deadline_to_domain(r) for r in results.all()]

    async def add(self, entity: Deadline) -> Deadline:
        orm = DeadlineORM(
            id=entity.id,
            matter_id=entity.matter_id,
            name=entity.name,
            due_at=entity.due_at,
            rule_id=entity.rule_id,
            status=entity.status,
            escalation_level=entity.escalation_level,
        )
        self._session.add(orm)
        await self._session.flush()
        return entity

    async def update(self, entity: Deadline) -> Deadline:
        existing = await self._session.get(DeadlineORM, entity.id)
        if existing is None:
            raise ValueError(f"Deadline {entity.id!r} not found.")
        existing.status = entity.status
        existing.escalation_level = entity.escalation_level
        await self._session.flush()
        return entity

    async def delete(self, id: str) -> None:
        existing = await self._session.get(DeadlineORM, id)
        if existing:
            await self._session.delete(existing)
            await self._session.flush()


class CommunicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, id: str) -> Communication | None:
        result = await self._session.get(CommunicationORM, id)
        return _communication_to_domain(result) if result else None

    async def list(self, **filters: Any) -> list[Communication]:
        stmt = select(CommunicationORM)
        results = await self._session.scalars(stmt)
        return [_communication_to_domain(r) for r in results.all()]

    async def add(self, entity: Communication) -> Communication:
        orm = CommunicationORM(
            id=entity.id,
            matter_id=entity.matter_id,
            direction=entity.direction,
            channel=entity.channel,
            subject=entity.subject,
            body=entity.body,
            status=entity.status,
            participants=[c.model_dump() for c in entity.participants],
        )
        self._session.add(orm)
        await self._session.flush()
        return entity

    async def update(self, entity: Communication) -> Communication:
        existing = await self._session.get(CommunicationORM, entity.id)
        if existing is None:
            raise ValueError(f"Communication {entity.id!r} not found.")
        existing.status = entity.status
        existing.participants = [c.model_dump() for c in entity.participants]
        await self._session.flush()
        return entity

    async def delete(self, id: str) -> None:
        existing = await self._session.get(CommunicationORM, id)
        if existing:
            await self._session.delete(existing)
            await self._session.flush()


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, id: str) -> Task | None:
        result = await self._session.get(TaskORM, id)
        return _task_to_domain(result) if result else None

    async def list(self, **filters: Any) -> list[Task]:
        stmt = select(TaskORM)
        results = await self._session.scalars(stmt)
        return [_task_to_domain(r) for r in results.all()]

    async def add(self, entity: Task) -> Task:
        orm = TaskORM(
            id=entity.id,
            matter_id=entity.matter_id,
            title=entity.title,
            assignee=entity.assignee,
            due_at=entity.due_at,
            status=entity.status,
        )
        self._session.add(orm)
        await self._session.flush()
        return entity

    async def update(self, entity: Task) -> Task:
        existing = await self._session.get(TaskORM, entity.id)
        if existing is None:
            raise ValueError(f"Task {entity.id!r} not found.")
        existing.title = entity.title
        existing.assignee = entity.assignee
        existing.due_at = entity.due_at
        existing.status = entity.status
        await self._session.flush()
        return entity

    async def delete(self, id: str) -> None:
        existing = await self._session.get(TaskORM, id)
        if existing:
            await self._session.delete(existing)
            await self._session.flush()


# ---------------------------------------------------------------------------
# AuditRepository — append-only; no update/delete
# ---------------------------------------------------------------------------


class AuditRepository:
    """Append-only; callers may not update or delete rows (spec §4.2)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, orm: AuditLogORM) -> AuditLogORM:
        self._session.add(orm)
        await self._session.flush()
        await self._session.refresh(orm)
        return orm

    async def get_latest(self) -> AuditLogORM | None:
        stmt = select(AuditLogORM).order_by(AuditLogORM.id.desc()).limit(1)
        return await self._session.scalar(stmt)  # type: ignore[no-any-return]

    async def get_range(
        self, start_id: int | None = None, end_id: int | None = None
    ) -> list[AuditLogORM]:
        stmt = select(AuditLogORM).order_by(AuditLogORM.id.asc())
        if start_id is not None:
            stmt = stmt.where(AuditLogORM.id >= start_id)
        if end_id is not None:
            stmt = stmt.where(AuditLogORM.id <= end_id)
        results = await self._session.scalars(stmt)
        return list(results.all())
