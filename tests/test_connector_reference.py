"""G6 + G7 — Reference adapters + contract harness tests."""

from __future__ import annotations

import pytest

from cam.connectors.errors import TransientError
from cam.connectors.reference import (
    ReferenceCaseConnector,
    ReferenceCRMConnector,
    ReferenceDocStoreConnector,
    ReferenceEmailConnector,
)
from tests.connectors.harness import run_contract



# a. Contract harness passes all reference adapters
async def test_contract_case() -> None:
    await run_contract(case=ReferenceCaseConnector())


async def test_contract_crm() -> None:
    await run_contract(crm=ReferenceCRMConnector())


async def test_contract_email() -> None:
    await run_contract(email=ReferenceEmailConnector())


async def test_contract_docstore() -> None:
    await run_contract(docstore=ReferenceDocStoreConnector())


# b. Harness fails a deliberately non-conforming stub
async def test_harness_fails_broken_stub() -> None:
    class BrokenCaseAdapter:
        async def get_matter(self, id: str):  # type: ignore[return]
            return None  # wrong: should raise NotFoundError for unknown ids

        async def create_matter(self, data):  # type: ignore[return]
            return None  # wrong: must return a Matter

        async def list_deadlines(self, matter_id: str):  # type: ignore[return]
            return []

    with pytest.raises(Exception):
        await run_contract(case=BrokenCaseAdapter())


# c. Email idempotency: same idem_key → same message_id
async def test_email_send_idempotency() -> None:
    adapter = ReferenceEmailConnector()
    import uuid
    from cam.core.domain.models import Communication

    comm = Communication(
        id=str(uuid.uuid4()), direction="out", channel="email",
        body="hello", status="draft",
    )
    draft_id = await adapter.create_draft(comm)
    key = str(uuid.uuid4())
    msg1 = await adapter.send(draft_id, key)
    msg2 = await adapter.send(draft_id, key)
    assert msg1 == msg2


# d. DocStore put→get checksum stable
async def test_docstore_checksum_stable() -> None:
    import hashlib, uuid
    from cam.core.domain.models import Document
    from datetime import datetime, timezone

    adapter = ReferenceDocStoreConnector()
    content = b"stable content"
    doc = Document(
        id=str(uuid.uuid4()), matter_id="m1", name="f.pdf",
        mime_type="application/pdf", uri="", version=1,
        privileged=True, checksum="", created_at=datetime.now(tz=timezone.utc),
    )
    stored = await adapter.put(doc, content)
    _, fetched_bytes = await adapter.get(stored.id)
    assert hashlib.sha256(fetched_bytes).hexdigest() == stored.checksum


# e. Fault injection: injected transient is raised
async def test_fault_injection_transient() -> None:
    adapter = ReferenceCaseConnector()
    adapter.inject_error(TransientError("reference_case", "forced transient"))
    import uuid
    from datetime import datetime, timezone
    from cam.core.domain.models import Contact
    from cam.connectors.ports import MatterDraft

    client = Contact(id=str(uuid.uuid4()), source="crm", name="X", external_ids={})
    draft = MatterDraft(
        reference="R", title="T", status="open",
        client=client, opened_at=datetime.now(tz=timezone.utc), external_ids={},
    )
    with pytest.raises(TransientError):
        await adapter.create_matter(draft)
