"""G1 — Domain model focused tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from cam.core.domain.models import (
    Communication,
    Contact,
    Deadline,
    Document,
    FeatureFlag,
    Matter,
    Task,
)

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_contact(**kwargs) -> Contact:  # type: ignore[return]
    defaults = dict(id="c1", source="crm", name="Ana Garcia", external_ids={})
    return Contact(**(defaults | kwargs))


# 1. Construct a valid instance of each model
def test_contact_valid() -> None:
    c = make_contact(email="ana@example.com", phone="+1-555-0100", role="client")
    assert c.id == "c1"
    assert c.email is not None


def test_matter_valid() -> None:
    client = make_contact()
    m = Matter(
        id="m1", source="case", reference="REF-001", title="Garcia Family",
        status="open", client=client, opened_at=NOW, external_ids={},
    )
    assert m.key_dates == []


def test_document_valid() -> None:
    d = Document(
        id="d1", matter_id="m1", name="I-130.pdf", mime_type="application/pdf",
        uri="s3://bucket/I-130.pdf", version=1, privileged=True,
        checksum="abc123", created_at=NOW,
    )
    assert d.privileged is True


def test_deadline_valid() -> None:
    dl = Deadline(id="dl1", matter_id="m1", name="RFE response", due_at=NOW)
    assert dl.status == "pending"
    assert dl.escalation_level == 0


def test_communication_valid() -> None:
    c = make_contact()
    comm = Communication(
        id="comm1", matter_id="m1", direction="out", channel="email",
        body="Welcome letter draft.", status="draft", participants=[c],
    )
    assert comm.direction == "out"


def test_task_valid() -> None:
    t = Task(id="t1", matter_id="m1", title="File I-130", status="open")
    assert t.assignee is None


# 2. Invalid enum value rejected
def test_deadline_invalid_status() -> None:
    with pytest.raises(ValidationError):
        Deadline(id="dl1", matter_id="m1", name="X", due_at=NOW, status="invalid_status")


def test_communication_invalid_direction() -> None:
    with pytest.raises(ValidationError):
        Communication(
            id="c1", direction="sideways", channel="email",
            body="X", status="draft",
        )


# 3. EmailStr / optional fields
def test_contact_invalid_email() -> None:
    with pytest.raises(ValidationError):
        make_contact(email="not-an-email")


def test_contact_email_none() -> None:
    c = make_contact(email=None)
    assert c.email is None


# 4. Forward-ref Matter.key_dates resolves
def test_matter_key_dates_type() -> None:
    dl = Deadline(id="dl1", matter_id="m1", name="RFE", due_at=NOW)
    client = make_contact()
    m = Matter(
        id="m1", source="case", reference="R", title="T",
        status="open", client=client, opened_at=NOW, key_dates=[dl],
    )
    assert isinstance(m.key_dates[0], Deadline)


# 5. FeatureFlag defaults to disabled
def test_feature_flag_default_disabled() -> None:
    ff = FeatureFlag(key="intake")
    assert ff.enabled is False
