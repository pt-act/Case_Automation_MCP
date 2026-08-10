"""Pack inspector endpoint tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from cam.sidecar.main import create_app


def _setup_packs() -> None:
    """Reset and register builtin packs for testing."""
    from cam.packs import register_builtin_packs, reset_registry, select_pack
    reset_registry()
    register_builtin_packs()
    select_pack("immigration")


def test_list_packs() -> None:
    """GET /packs returns all registered packs."""
    _setup_packs()
    app = create_app()
    client = TestClient(app)

    response = client.get("/packs")
    assert response.status_code == 200
    data = response.json()
    assert "packs" in data
    names = [p["name"] for p in data["packs"]]
    assert "immigration" in names
    assert "consulting" in names


def test_list_packs_marks_active() -> None:
    """GET /packs flags the active pack."""
    _setup_packs()
    app = create_app()
    client = TestClient(app)

    response = client.get("/packs")
    data = response.json()
    active = [p for p in data["packs"] if p.get("is_active")]
    assert len(active) == 1
    assert active[0]["name"] == "immigration"


def test_inspect_immigration_pack() -> None:
    """GET /packs/immigration/inspect returns all config surfaces."""
    _setup_packs()
    app = create_app()
    client = TestClient(app)

    response = client.get("/packs/immigration/inspect")
    assert response.status_code == 200
    data = response.json()

    assert data["name"] == "immigration"
    assert data["is_active"] is True
    assert "terminology" in data
    assert data["terminology"]["matter"] == "Matter"
    assert "case_types" in data
    assert len(data["case_types"]) > 0
    assert "restriction_policy" in data
    assert data["restriction_policy"]["label"] == "Privileged"
    assert data["restriction_policy"]["default_restricted"] is True
    assert "pii_patterns" in data
    assert data["pii_patterns"]["patterns_redacted"] is True


def test_inspect_unknown_pack_404() -> None:
    """GET /packs/nonexistent/inspect returns 404 with registered names."""
    _setup_packs()
    app = create_app()
    client = TestClient(app)

    response = client.get("/packs/nonexistent/inspect")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["error"] == "pack_not_found"
    assert detail["requested"] == "nonexistent"
    assert "immigration" in detail["registered"]


def test_pii_patterns_are_counts_only() -> None:
    """PII patterns are counts + labels, never regex strings."""
    _setup_packs()
    app = create_app()
    client = TestClient(app)

    response = client.get("/packs/immigration/inspect")
    data = response.json()
    pii = data["pii_patterns"]

    assert "baseline_count" in pii
    assert "pack_addition_count" in pii
    assert "addition_labels" in pii
    assert pii["patterns_redacted"] is True
    # No regex patterns in the response
    import json
    full_text = json.dumps(data)
    assert "\\d" not in full_text  # no regex digit patterns
    assert "regex" not in full_text.lower()


def test_inspect_includes_rbac_roles() -> None:
    """Inspect response includes RBAC roles."""
    _setup_packs()
    app = create_app()
    client = TestClient(app)

    response = client.get("/packs/immigration/inspect")
    data = response.json()
    assert "rbac_roles" in data
    assert len(data["rbac_roles"]) > 0
    assert "attorney" in data["rbac_roles"]


def test_inspect_includes_deadline_rules() -> None:
    """Inspect response includes deadline rule count + IDs."""
    _setup_packs()
    app = create_app()
    client = TestClient(app)

    response = client.get("/packs/immigration/inspect")
    data = response.json()
    assert data["deadline_rules_count"] > 0
    assert len(data["deadline_rule_ids"]) == data["deadline_rules_count"]
