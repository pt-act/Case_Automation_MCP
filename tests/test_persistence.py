"""G2 — Persistence focused tests (require live Postgres via CAM_DATABASE_URL)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from cam.core.domain.models import Contact, Deadline, Document, Matter, Task
from cam.persistence.repositories import (
    ContactRepository,
    DeadlineRepository,
    DocumentRepository,
    MatterRepository,
    TaskRepository,
)


NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def uid() -> str:
    return str(uuid.uuid4())


def make_contact(id: str | None = None) -> Contact:
    return Contact(id=id or uid(), source="crm", name="Ana Garcia", external_ids={})


async def persist_contact(session: AsyncSession, contact: Contact) -> Contact:
    repo = ContactRepository(session)
    return await repo.add(contact)


# 1. CRUD round-trip for Contact
async def test_contact_crud(db_session: AsyncSession) -> None:
    repo = ContactRepository(db_session)
    c = make_contact()
    await repo.add(c)

    fetched = await repo.get(c.id)
    assert fetched is not None
    assert fetched.name == "Ana Garcia"
    assert isinstance(fetched, Contact)  # domain model, not ORM


# 2. Read returns domain model (not ORM)
async def test_read_returns_domain_model(db_session: AsyncSession) -> None:
    repo = ContactRepository(db_session)
    c = make_contact()
    await repo.add(c)
    result = await repo.get(c.id)
    assert type(result).__name__ == "Contact"


# 3. FK enforced (matter without contact fails)
async def test_matter_fk_enforced(db_session: AsyncSession) -> None:
    from sqlalchemy.exc import IntegrityError
    from cam.persistence.repositories import MatterRepository

    repo = MatterRepository(db_session)
    client_id_that_doesnt_exist = uid()
    ghost_contact = Contact(id=client_id_that_doesnt_exist, source="crm", name="Ghost", external_ids={})
    m = Matter(
        id=uid(), source="case", reference="R", title="T",
        status="open", client=ghost_contact, opened_at=NOW, external_ids={},
    )
    with pytest.raises(Exception):  # IntegrityError from FK violation
        await repo.add(m)


# 4. JSONB round-trips a dict
async def test_jsonb_external_ids_roundtrip(db_session: AsyncSession) -> None:
    repo = ContactRepository(db_session)
    c = make_contact()
    c = c.model_copy(update={"external_ids": {"crm": "CRM-001", "case": "CASE-999"}})
    await repo.add(c)
    fetched = await repo.get(c.id)
    assert fetched is not None
    assert fetched.external_ids == {"crm": "CRM-001", "case": "CASE-999"}


# 5. Task CRUD
async def test_task_crud(db_session: AsyncSession) -> None:
    contact = make_contact()
    await ContactRepository(db_session).add(contact)

    matter = Matter(
        id=uid(), source="case", reference="R", title="T",
        status="open", client=contact, opened_at=NOW, external_ids={},
    )
    await MatterRepository(db_session).add(matter)

    task = Task(id=uid(), matter_id=matter.id, title="File I-130", status="open")
    task_repo = TaskRepository(db_session)
    await task_repo.add(task)

    fetched = await task_repo.get(task.id)
    assert fetched is not None
    assert fetched.title == "File I-130"
    assert isinstance(fetched, Task)
