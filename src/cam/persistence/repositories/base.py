from __future__ import annotations

from typing import Any, Protocol, TypeVar

from cam.core.domain.models import Communication, Contact, Deadline, Document, Matter, Task
from cam.persistence.models import (
    CommunicationORM,
    ContactORM,
    DeadlineORM,
    DocumentORM,
    MatterORM,
    TaskORM,
)

T = TypeVar("T")


class Repository(Protocol[T]):
    async def get(self, id: str) -> T | None: ...
    async def list(self, **filters: Any) -> list[T]: ...
    async def add(self, entity: T) -> T: ...
    async def update(self, entity: T) -> T: ...
    async def delete(self, id: str) -> None: ...


def _contact_to_domain(orm: ContactORM) -> Contact:
    return Contact(
        id=orm.id, source=orm.source, name=orm.name,
        email=orm.email,
        phone=orm.phone, role=orm.role,
        external_ids=orm.external_ids or {},
    )


def _contact_from_domain(domain: Contact) -> ContactORM:
    return ContactORM(
        id=domain.id, source=domain.source, name=domain.name,
        email=str(domain.email) if domain.email else None,
        phone=domain.phone, role=domain.role,
        external_ids=domain.external_ids,
    )


def _deadline_to_domain(orm: DeadlineORM) -> Deadline:
    from cam.core.domain.models import Deadline
    return Deadline(
        id=orm.id, matter_id=orm.matter_id, name=orm.name,
        due_at=orm.due_at, rule_id=orm.rule_id,
        status=orm.status, escalation_level=orm.escalation_level,
    )


def _document_to_domain(orm: DocumentORM) -> Document:
    return Document(
        id=orm.id, matter_id=orm.matter_id, name=orm.name,
        mime_type=orm.mime_type, uri=orm.uri,
        classification=orm.classification, version=orm.version,
        privileged=orm.privileged, checksum=orm.checksum, created_at=orm.created_at,
    )


def _communication_to_domain(orm: CommunicationORM) -> Communication:
    participants = [Contact.model_validate(p) for p in (orm.participants or [])]
    return Communication(
        id=orm.id, matter_id=orm.matter_id, direction=orm.direction,
        channel=orm.channel, subject=orm.subject, body=orm.body,
        status=orm.status, participants=participants,
    )


def _task_to_domain(orm: TaskORM) -> type:
    return Task(
        id=orm.id, matter_id=orm.matter_id, title=orm.title,
        assignee=orm.assignee, due_at=orm.due_at, status=orm.status,
    )


def _matter_to_domain(orm: MatterORM) -> Matter:
    client = _contact_to_domain(orm.client_rel)
    key_dates = [_deadline_to_domain(d) for d in (orm.deadlines or [])]
    return Matter(
        id=orm.id, source=orm.source, reference=orm.reference, title=orm.title,
        status=orm.status, practice_area=orm.practice_area, client=client,
        responsible=orm.responsible, opened_at=orm.opened_at,
        key_dates=key_dates, external_ids=orm.external_ids or {},
    )
