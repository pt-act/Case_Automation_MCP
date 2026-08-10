"""G1 — Port protocols + registry focused tests."""

from __future__ import annotations

import pytest

from cam.connectors.ports import (
    CaseConnector,
    CRMConnector,
    DocStoreConnector,
    EmailConnector,
    MatterDraft,
)
from cam.connectors.reference import (
    ReferenceCaseConnector,
    ReferenceCRMConnector,
    ReferenceDocStoreConnector,
    ReferenceEmailConnector,
)
from cam.connectors.registry import (
    ConnectorNotFoundError,
    clear_registry,
    get_connector,
    register_connector,
)


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_registry()


# 1a. Reference adapters satisfy each Protocol (runtime isinstance check)
def test_reference_case_satisfies_protocol() -> None:
    assert isinstance(ReferenceCaseConnector(), CaseConnector)


def test_reference_crm_satisfies_protocol() -> None:
    assert isinstance(ReferenceCRMConnector(), CRMConnector)


def test_reference_email_satisfies_protocol() -> None:
    assert isinstance(ReferenceEmailConnector(), EmailConnector)


def test_reference_docstore_satisfies_protocol() -> None:
    assert isinstance(ReferenceDocStoreConnector(), DocStoreConnector)


# 1b. MatterDraft rejects id field
def test_matter_draft_has_no_id_field() -> None:
    # MatterDraft must NOT have an id field
    assert "id" not in MatterDraft.model_fields
    assert "source" not in MatterDraft.model_fields


# 1c. Registry returns registered adapter
def test_registry_returns_registered_adapter() -> None:
    adapter = ReferenceCaseConnector()
    register_connector("myvendor", case=adapter)
    result = get_connector("myvendor", "case")
    assert result is adapter


# 1d. Registry raises on unknown name/category
def test_registry_raises_unknown_name() -> None:
    with pytest.raises(ConnectorNotFoundError):
        get_connector("no-such-connector", "case")


def test_registry_raises_missing_category() -> None:
    register_connector("vendor-no-crm", case=ReferenceCaseConnector())
    with pytest.raises(ConnectorNotFoundError):
        get_connector("vendor-no-crm", "crm")


# 1e. Multiple adapters under one name
def test_registry_multiple_categories() -> None:
    case = ReferenceCaseConnector()
    crm = ReferenceCRMConnector()
    register_connector("multi", case=case, crm=crm)
    assert get_connector("multi", "case") is case
    assert get_connector("multi", "crm") is crm
