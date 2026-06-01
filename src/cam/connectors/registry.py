"""Pluggable connector registry — spec §4.5.

Registering a new adapter requires no edits to core; the adapter calls
`register_connector()` at import time (or from a startup hook).
"""

from __future__ import annotations

from typing import Any, Literal

from cam.connectors.ports import CaseConnector, CRMConnector, DocStoreConnector, EmailConnector

Category = Literal["case", "crm", "email", "docstore"]

_REGISTRY: dict[str, dict[str, Any]] = {}


class ConnectorNotFound(KeyError):
    def __init__(self, name: str, category: Category) -> None:
        super().__init__(f"No {category!r} connector registered under name {name!r}.")
        self.connector_name = name
        self.category = category


def register_connector(
    name: str,
    *,
    case: CaseConnector | None = None,
    crm: CRMConnector | None = None,
    email: EmailConnector | None = None,
    docstore: DocStoreConnector | None = None,
    normaliser: object | None = None,
    secret_ref: str | None = None,
) -> None:
    """Register one or more adapter implementations under a logical name.

    Adding an adapter = calling this function; no core code changes needed.
    """
    entry = _REGISTRY.setdefault(name, {})
    if case is not None:
        entry["case"] = case
    if crm is not None:
        entry["crm"] = crm
    if email is not None:
        entry["email"] = email
    if docstore is not None:
        entry["docstore"] = docstore
    if normaliser is not None:
        entry["_normaliser"] = normaliser
    if secret_ref is not None:
        entry["_secret_ref"] = secret_ref


def get_connector(name: str, category: Category) -> object:
    """Retrieve a registered adapter.  Raises ConnectorNotFound if absent."""
    entry = _REGISTRY.get(name, {})
    adapter = entry.get(category)
    if adapter is None:
        raise ConnectorNotFound(name, category)
    return adapter


def get_normaliser(name: str) -> object | None:
    return _REGISTRY.get(name, {}).get("_normaliser")


def registered_connectors() -> list[str]:
    return list(_REGISTRY.keys())


def clear_registry() -> None:
    """Reset the registry (test helper only)."""
    _REGISTRY.clear()
