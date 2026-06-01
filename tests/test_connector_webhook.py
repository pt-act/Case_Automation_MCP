"""G4 — Webhook pipeline focused tests (no live network)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from cam.connectors.webhook.models import Event
from cam.connectors.webhook.pipeline import process_webhook, reference_normaliser, verify_signature


def _sig(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


SECRET = "test-webhook-secret"


def _body(event_type: str = "matter.status_changed", **kwargs) -> bytes:  # type: ignore[return]
    return json.dumps({"id": "evt-001", "type": event_type, **kwargs}).encode()


def _headers(body: bytes, secret: str = SECRET) -> dict:
    return {"x-hub-signature-256": _sig(body, secret)}


# Mock TriggerSink
class _Sink:
    def __init__(self) -> None:
        self.calls: list[Event] = []

    async def enqueue(self, event: Event) -> str:
        self.calls.append(event)
        return "trigger-1"


# Mock Redis (in-memory)
class _Redis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int = 0) -> None:
        self._store[key] = value


# a. Valid signature passes
@pytest.mark.asyncio
async def test_valid_signature_enqueues() -> None:
    body = _body()
    sink = _Sink()
    redis = _Redis()
    result = await process_webhook(
        connector_name="reference",
        raw_body=body,
        headers=_headers(body),
        secret=SECRET,
        normaliser=reference_normaliser,
        trigger_sink=sink,
        redis_client=redis,
    )
    assert result.decision == "enqueued"
    assert len(sink.calls) == 1


# b. Invalid/missing signature → rejected, no enqueue
@pytest.mark.asyncio
async def test_invalid_signature_rejected() -> None:
    body = _body()
    sink = _Sink()
    result = await process_webhook(
        connector_name="reference",
        raw_body=body,
        headers={"x-hub-signature-256": "badhash"},
        secret=SECRET,
        normaliser=reference_normaliser,
        trigger_sink=sink,
        redis_client=_Redis(),
    )
    assert result.decision == "rejected_bad_signature"
    assert len(sink.calls) == 0


@pytest.mark.asyncio
async def test_missing_signature_rejected() -> None:
    body = _body()
    sink = _Sink()
    result = await process_webhook(
        connector_name="reference",
        raw_body=body,
        headers={},
        secret=SECRET,
        normaliser=reference_normaliser,
        trigger_sink=sink,
        redis_client=_Redis(),
    )
    assert result.decision == "rejected_bad_signature"
    assert len(sink.calls) == 0


# c. Stale timestamp rejected
def test_stale_timestamp_rejected() -> None:
    body = b"test"
    assert not verify_signature(body, _sig(body, SECRET), SECRET, timestamp_header="1000")


# d. Known event type → Event
@pytest.mark.asyncio
async def test_known_type_produces_event() -> None:
    body = _body("lead.created")
    sink = _Sink()
    result = await process_webhook(
        connector_name="reference",
        raw_body=body,
        headers=_headers(body),
        secret=SECRET,
        normaliser=reference_normaliser,
        trigger_sink=sink,
        redis_client=_Redis(),
    )
    assert result.decision == "enqueued"
    assert sink.calls[0].type == "lead.created"


# e. Unknown event type → dropped (202), no enqueue
@pytest.mark.asyncio
async def test_unknown_type_dropped() -> None:
    body = _body("totally.unknown.type")
    sink = _Sink()
    result = await process_webhook(
        connector_name="reference",
        raw_body=body,
        headers=_headers(body),
        secret=SECRET,
        normaliser=reference_normaliser,
        trigger_sink=sink,
        redis_client=_Redis(),
    )
    assert result.decision == "dropped_unknown_type"
    assert len(sink.calls) == 0


# f. Duplicate event id → single enqueue
@pytest.mark.asyncio
async def test_duplicate_event_single_enqueue() -> None:
    body = _body()
    sink = _Sink()
    redis = _Redis()
    for _ in range(2):
        await process_webhook(
            connector_name="reference",
            raw_body=body,
            headers=_headers(body),
            secret=SECRET,
            normaliser=reference_normaliser,
            trigger_sink=sink,
            redis_client=redis,
        )
    assert len(sink.calls) == 1


# g. TriggerSink called exactly once per unique event
@pytest.mark.asyncio
async def test_trigger_sink_called_once() -> None:
    body = _body()
    sink = _Sink()
    result = await process_webhook(
        connector_name="reference",
        raw_body=body,
        headers=_headers(body),
        secret=SECRET,
        normaliser=reference_normaliser,
        trigger_sink=sink,
        redis_client=_Redis(),
    )
    assert result.decision == "enqueued"
    assert len(sink.calls) == 1
