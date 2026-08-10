"""Contract-test harness — spec §7.1.

`run_contract(adapter_set)` is the conformance suite every adapter must pass.
Reference adapters must pass it; a deliberately non-conforming stub must fail.

Invoke via the individual test files or directly::

    from tests.connectors.harness import run_contract
    run_contract(case=MyClioAdapter(), crm=..., email=..., docstore=...)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from cam.connectors.ports import MatterDraft
from cam.core.domain.models import ACL, Communication, Contact, Document, Matter

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _contact(suffix: str = "") -> Contact:
    return Contact(
        id=str(uuid.uuid4()),
        source="test",
        name=f"Test User{suffix}",
        email=None,
        phone=None,
        role="client",
        external_ids={},
    )


def _draft_comm(recipients: list[Contact]) -> Communication:
    return Communication(
        id=str(uuid.uuid4()),
        matter_id=None,
        direction="out",
        channel="email",
        subject="Test Subject",
        body="Hello",
        status="draft",
        participants=recipients,
    )


def _document(matter_id: str) -> Document:
    return Document(
        id=str(uuid.uuid4()),
        matter_id=matter_id,
        name="test.pdf",
        mime_type="application/pdf",
        uri="",
        classification=None,
        version=1,
        privileged=True,
        checksum="",
        created_at=NOW,
    )


# ---------------------------------------------------------------------------
# Per-category contract suites
# ---------------------------------------------------------------------------


async def contract_case(adapter: object) -> None:
    """CaseConnector conformance: get_matter, create_matter, list_deadlines."""
    client = _contact()
    draft = MatterDraft(
        reference="REF-CONTRACT-001",
        title="Contract Test Matter",
        status="open",
        practice_area=None,
        client=client,
        responsible=None,
        opened_at=NOW,
        external_ids={},
    )
    matter: Matter = await adapter.create_matter(draft)  # type: ignore[attr-defined]
    assert matter.id, "create_matter must return a matter with an id"
    assert matter.reference == "REF-CONTRACT-001"

    fetched: Matter = await adapter.get_matter(matter.id)  # type: ignore[attr-defined]
    assert fetched.id == matter.id

    deadlines = await adapter.list_deadlines(matter.id)  # type: ignore[attr-defined]
    assert isinstance(deadlines, list)

    # NotFoundError on unknown id
    from cam.connectors.errors import NotFoundError
    with pytest.raises(NotFoundError):
        await adapter.get_matter("nonexistent-id")  # type: ignore[attr-defined]


async def contract_crm(adapter: object) -> None:
    """CRMConnector conformance: upsert_contact, find_contact."""
    c = _contact("-crm")
    upserted = await adapter.upsert_contact(c)  # type: ignore[attr-defined]
    assert upserted.id, "upsert_contact must return a contact with an id"

    results = await adapter.find_contact(c.name)  # type: ignore[attr-defined]
    assert any(r.id == upserted.id for r in results), "find_contact must return upserted contact"

    empty = await adapter.find_contact("zzz-no-match-xyz")  # type: ignore[attr-defined]
    assert isinstance(empty, list)


async def contract_email(adapter: object) -> None:
    """EmailConnector conformance: create_draft → send (idempotent)."""
    recipients = [_contact("-email")]
    comm = _draft_comm(recipients)
    draft_id = await adapter.create_draft(comm)  # type: ignore[attr-defined]
    assert draft_id, "create_draft must return a draft id"

    idem_key = str(uuid.uuid4())
    msg_id_1 = await adapter.send(draft_id, idem_key)  # type: ignore[attr-defined]
    assert msg_id_1, "send must return a message id"

    # Idempotency: same idem_key → same message_id
    msg_id_2 = await adapter.send(draft_id, idem_key)  # type: ignore[attr-defined]
    assert msg_id_1 == msg_id_2, "send must be idempotent on idem_key"


async def contract_docstore(adapter: object) -> None:
    """DocStoreConnector conformance: put → get (checksum stable) → move."""
    content = b"contract test document content"
    doc = _document(matter_id="m-contract")
    stored = await adapter.put(doc, content)  # type: ignore[attr-defined]
    assert stored.id, "put must return a document with an id"
    assert stored.checksum, "put must compute a checksum"

    fetched_doc, fetched_bytes = await adapter.get(stored.id)  # type: ignore[attr-defined]
    assert fetched_bytes == content, "get must return the same bytes as put"
    assert fetched_doc.checksum == stored.checksum, "checksum must be stable"

    acl = ACL(principals=["user-1"], permission="read", external=False)
    moved = await adapter.move(stored.id, "matter/m-contract/docs", acl)  # type: ignore[attr-defined]
    assert stored.id == moved.id, "move must return the same document id"
    assert "matter/m-contract/docs" in moved.uri or moved.uri != stored.uri


async def run_contract(
    *,
    case: object | None = None,
    crm: object | None = None,
    email: object | None = None,
    docstore: object | None = None,
) -> None:
    """Run all applicable conformance suites for the supplied adapters."""
    if case is not None:
        await contract_case(case)
    if crm is not None:
        await contract_crm(crm)
    if email is not None:
        await contract_email(email)
    if docstore is not None:
        await contract_docstore(docstore)
