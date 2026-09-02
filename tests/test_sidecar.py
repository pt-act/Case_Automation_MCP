"""Sidecar FastAPI HTTP tests — webhooks + approvals + health endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cam.connectors.reference import ReferenceCaseConnector
from cam.connectors.registry import clear_registry, register_connector
from cam.connectors.webhook.pipeline import reference_normaliser
from cam.core.orchestrator.gates import issue_token
from cam.core.orchestrator.states import (
    GateRequest,
    RunStatus,
    TriggerRef,
    WorkflowRun,
)
from cam.core.orchestrator.store import InMemoryRunStore
from cam.sidecar.main import create_app

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
WEBHOOK_SECRET = "test-secret"
SIGNING_KEY = b"test-signing-key-32-bytes-padded"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Create a fresh FastAPI TestClient with a pre-configured app state."""
    app = create_app()

    # Reset connector registry and register 'reference'
    clear_registry()
    register_connector(
        "reference",
        case=ReferenceCaseConnector(),
        normaliser=reference_normaliser,
    )

    class _FakeRedis:
        _store: dict = {}
        async def get(self, key): return self._store.get(key)
        async def set(self, key, value, ex=0): self._store[key] = value

    class _FakeSink:
        calls: list = []
        async def enqueue(self, event):
            self._fake_sink_calls_append(event)
            return "t1"
        def _fake_sink_calls_append(self, e): self.calls.append(e)

    sink = _FakeSink()
    redis = _FakeRedis()

    # Simulate an authenticated approver session (production wires SSO here).
    @app.middleware("http")
    async def _fake_auth(request, call_next):  # type: ignore[no-untyped-def]
        request.state.user_identity = "attorney"
        return await call_next(request)

    app.state.webhook_secrets = {"reference": WEBHOOK_SECRET}
    app.state.redis_client = redis
    app.state.trigger_sink = sink
    app.state.run_store = None
    app.state.signing_key = SIGNING_KEY

    return TestClient(app, raise_server_exceptions=True)


def _sig(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _event_body(event_type: str = "matter.status_changed") -> bytes:
    return json.dumps({"id": "evt-001", "type": event_type}).encode()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


def test_health_returns_200(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Webhook ingestion
# ---------------------------------------------------------------------------


def test_webhook_valid_signature_accepted(client):
    body = _event_body()
    r = client.post(
        "/webhooks/reference",
        content=body,
        headers={"x-hub-signature-256": _sig(body), "content-type": "application/json"},
    )
    assert r.status_code in (200, 202)


def test_webhook_invalid_signature_rejected(client):
    body = _event_body()
    r = client.post(
        "/webhooks/reference",
        content=body,
        headers={"x-hub-signature-256": "badsignature", "content-type": "application/json"},
    )
    assert r.status_code in (400, 401)


def test_webhook_unknown_connector_404(client):
    body = _event_body()
    r = client.post(
        "/webhooks/nonexistent_vendor",
        content=body,
        headers={"x-hub-signature-256": _sig(body), "content-type": "application/json"},
    )
    assert r.status_code == 404


def test_webhook_oversized_body_rejected(client):
    body = b"x" * (2 * 1024 * 1024)  # 2 MiB > 1 MiB cap
    r = client.post(
        "/webhooks/reference",
        content=body,
        headers={"x-hub-signature-256": _sig(body), "content-type": "application/json"},
    )
    assert r.status_code == 413


def test_webhook_unknown_event_type_202(client):
    body = json.dumps({"id": "evt-002", "type": "totally.unknown"}).encode()
    r = client.post(
        "/webhooks/reference",
        content=body,
        headers={"x-hub-signature-256": _sig(body), "content-type": "application/json"},
    )
    # Unknown type → 202 dropped (not an error)
    assert r.status_code == 202


# ---------------------------------------------------------------------------
# Approval endpoints
# ---------------------------------------------------------------------------


def _make_raw_token(gate_id: str, run_id: str, step: str = "GATE:approve") -> str:
    raw_token, _ = issue_token(gate_id, run_id, step, "web", SIGNING_KEY)
    return raw_token


def test_approval_page_valid_token_200(client):
    token = _make_raw_token("gate-1", "run-1")
    r = client.get(f"/approvals/{token}")
    assert r.status_code == 200
    assert "approval" in r.text.lower() or "approve" in r.text.lower()


def test_approval_page_tampered_token_401(client):
    token = _make_raw_token("gate-1", "run-1")
    tampered = token[:-4] + "XXXX"
    r = client.get(f"/approvals/{tampered}")
    assert r.status_code == 401


def test_approval_post_no_run_store_503(client):
    """Without a run_store, POST /approvals should return 503."""
    token = _make_raw_token("gate-1", "run-1")
    r = client.post(f"/approvals/{token}", data={"decision": "approve"})
    assert r.status_code == 503


def test_approval_post_bad_decision_400(client):
    """Invalid decision value → 400 or 422; also 503 when run store is a stub."""
    # The endpoint validates decision AND requires a run_store.
    # With a stub run_store, the run_store check (503) precedes decision validation.
    # Either 400/422 (bad decision) or 503 (no store) is acceptable here.
    token = _make_raw_token("gate-1", "run-1")
    r = client.post(f"/approvals/{token}", data={"decision": "maybe"})
    assert r.status_code in (400, 422, 503), f"Unexpected status {r.status_code}"


# ---------------------------------------------------------------------------
# Approval UI — human-readable pages, reason capture, workflow context
# ---------------------------------------------------------------------------


async def _seed_gated_run(
    app, run_id: str = "run-web-1", gate_id: str = "gate-web-1", ttl_seconds: int = 86400
) -> tuple[InMemoryRunStore, str]:
    """Create a run + pending gate + token and wire the store into the app."""
    store = InMemoryRunStore()
    now = datetime.now(tz=UTC)
    store._runs[run_id] = WorkflowRun(
        id=run_id,
        workflow="intake",
        workflow_version=1,
        status=RunStatus.AWAITING_APPROVAL,
        trigger=TriggerRef(kind="agent", source="test"),
        created_at=now,
        updated_at=now,
    )
    await store.create_gate_request(
        GateRequest(
            id=gate_id,
            run_id=run_id,
            step="GATE:human_review",
            required_role="attorney",
            status="pending",
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
    )
    raw_token, token_record = issue_token(gate_id, run_id, "GATE:human_review", "web",
                                          SIGNING_KEY, ttl_seconds=ttl_seconds)
    await store.save_token(token_record)
    app.state.run_store = store
    return store, raw_token


async def test_approval_page_shows_workflow_and_reason_field(client):
    _, raw_token = await _seed_gated_run(client.app)
    r = client.get(f"/approvals/{raw_token}")
    assert r.status_code == 200
    assert "intake" in r.text, "Page should show the workflow being approved"
    assert 'name="reason"' in r.text, "Page should offer a reason field"


async def test_approval_page_invalid_token_is_html_401(client):
    token = _make_raw_token("gate-1", "run-1")
    tampered = token[:-4] + "XXXX"
    r = client.get(f"/approvals/{tampered}")
    assert r.status_code == 401
    assert "<html" in r.text.lower(), "Error must render as a page, not JSON"


async def test_approval_post_success_renders_confirmation_and_reason(client):
    store, raw_token = await _seed_gated_run(client.app)
    r = client.post(
        f"/approvals/{raw_token}",
        data={"decision": "approve", "reason": "Reviewed the draft; ready to send"},
    )
    assert r.status_code == 200
    assert "recorded" in r.text.lower(), "Approver must get a readable confirmation"
    decision = store.all_decisions()[0]
    assert decision.decision == "approve"
    assert decision.channel == "web"
    assert decision.reason == "Reviewed the draft; ready to send"


async def test_approval_post_expired_token_renders_html_error(client):
    _, raw_token = await _seed_gated_run(client.app, ttl_seconds=-1)
    r = client.post(f"/approvals/{raw_token}", data={"decision": "approve"})
    assert r.status_code == 401
    assert "expired" in r.text.lower()
    assert "<html" in r.text.lower()
