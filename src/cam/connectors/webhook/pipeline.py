"""Webhook ingestion pipeline: verify → normalise → dedupe → enqueue — spec §4.4, §5.3.

State transitions per inbound request:
  received → verified → normalised → (deduped? drop : enqueued)
  Terminal states: enqueued | duplicate_ignored | dropped_unknown_type | rejected_bad_signature
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import structlog

from cam.connectors.webhook.models import Event

log = structlog.get_logger(__name__)

MAX_BODY_BYTES = 1_048_576  # 1 MiB — ASSUMPTION (confirm)
DEDUP_TTL_SECONDS = 7 * 24 * 3600  # 7 days — ASSUMPTION (confirm)
REPLAY_WINDOW_SECONDS = 300  # 5 min timestamp window — ASSUMPTION (confirm)


# ---------------------------------------------------------------------------
# Pipeline result type
# ---------------------------------------------------------------------------


class PipelineResult:
    __slots__ = ("decision", "event", "reason")

    def __init__(
        self,
        decision: str,
        event: Event | None = None,
        reason: str = "",
    ) -> None:
        self.decision = decision
        self.event = event
        self.reason = reason


# ---------------------------------------------------------------------------
# HMAC verification
# ---------------------------------------------------------------------------


def verify_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
    timestamp_header: str | None = None,
) -> bool:
    """Constant-time HMAC-SHA256 verification with optional replay-window check.

    Signature format assumed: hex digest of HMAC-SHA256(secret, body).
    Adapters override this for vendor-specific signature schemes.
    """
    if not signature_header:
        return False

    # Replay window: if a timestamp header is present and stale, reject.
    if timestamp_header is not None:
        try:
            ts = float(timestamp_header)
            now = datetime.now(tz=timezone.utc).timestamp()
            if abs(now - ts) > REPLAY_WINDOW_SECONDS:
                return False
        except (ValueError, TypeError):
            return False

    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Dedup (Redis)
# ---------------------------------------------------------------------------


def _dedup_key(connector: str, provider_event_id: str) -> str:
    return f"cam:webhook:{connector}:{provider_event_id}"


async def is_duplicate(redis_client: Any, connector: str, provider_event_id: str) -> bool:
    result = await redis_client.get(_dedup_key(connector, provider_event_id))
    return result is not None


async def mark_processed(
    redis_client: Any, connector: str, provider_event_id: str
) -> None:
    await redis_client.set(
        _dedup_key(connector, provider_event_id),
        "1",
        ex=DEDUP_TTL_SECONDS,
    )


# ---------------------------------------------------------------------------
# Main pipeline function
# ---------------------------------------------------------------------------


async def process_webhook(
    *,
    connector_name: str,
    raw_body: bytes,
    headers: dict[str, str],
    secret: str,
    normaliser: Callable[[str, dict], Event | None],
    trigger_sink: Any,  # TriggerSink protocol
    redis_client: Any,
) -> PipelineResult:
    """Run the full verify → normalise → dedupe → enqueue pipeline.

    Returns a PipelineResult describing the terminal decision.
    Never logs request/response bodies or PII.
    """
    # Size cap
    if len(raw_body) > MAX_BODY_BYTES:
        log.warning("webhook.oversized", connector=connector_name, size=len(raw_body))
        return PipelineResult("rejected_oversized", reason="Body exceeds size cap.")

    # 1. Verify
    sig = headers.get("x-hub-signature-256") or headers.get("x-signature")
    ts = headers.get("x-timestamp")
    if not verify_signature(raw_body, sig, secret, ts):
        log.warning("webhook.bad_signature", connector=connector_name)
        _emit_metric(connector_name, "rejected_bad_signature")
        return PipelineResult("rejected_bad_signature", reason="Invalid or missing signature.")

    # 2. Parse raw body (JSON only for now)
    try:
        raw_payload: dict = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        log.warning("webhook.malformed_body", connector=connector_name)
        return PipelineResult("rejected_bad_signature", reason="Malformed JSON body.")

    # 3. Normalise
    event = normaliser(connector_name, raw_payload)
    if event is None:
        log.info("webhook.unknown_type", connector=connector_name)
        _emit_metric(connector_name, "dropped_unknown_type")
        return PipelineResult("dropped_unknown_type", reason="Unrecognised event type.")

    # 4. Deduplicate (mark-processed BEFORE enqueue for crash safety)
    try:
        if await is_duplicate(redis_client, connector_name, event.provider_event_id):
            log.info(
                "webhook.duplicate",
                connector=connector_name,
                provider_event_id_hash=hashlib.sha256(
                    event.provider_event_id.encode()
                ).hexdigest()[:12],
            )
            _emit_metric(connector_name, "duplicate_ignored")
            return PipelineResult("duplicate_ignored")

        await mark_processed(redis_client, connector_name, event.provider_event_id)
    except Exception as exc:
        log.error("webhook.redis_error", connector=connector_name, error=str(exc))
        # Redis down → fail closed (return 503 to provider; spec §6)
        return PipelineResult("redis_unavailable", reason="Dedup store unavailable.")

    # 5. Enqueue
    try:
        await trigger_sink.enqueue(event)
    except Exception as exc:
        log.error("webhook.enqueue_error", connector=connector_name, error=str(exc))
        return PipelineResult("enqueue_failed", reason="Trigger sink unavailable.")

    log.info("webhook.enqueued", connector=connector_name, event_type=event.type)
    _emit_metric(connector_name, "enqueued")
    return PipelineResult("enqueued", event=event)


# ---------------------------------------------------------------------------
# Reference normaliser (returns None for unknown types)
# ---------------------------------------------------------------------------


def reference_normaliser(connector: str, raw: dict) -> Event | None:
    """Default normaliser for the in-memory reference connector.
    Recognises a small set of standard event types; all else → None.
    """
    event_type = raw.get("type") or raw.get("event_type")
    if not event_type:
        return None

    known = {
        "matter.status_changed",
        "lead.created",
        "document.signed",
        "email.received",
        "contact.created",
    }
    if event_type not in known:
        return None

    return Event(
        id=str(uuid.uuid4()),
        connector=connector,
        provider_event_id=raw.get("id", str(uuid.uuid4())),
        type=event_type,
        occurred_at=datetime.now(tz=timezone.utc),
        received_at=datetime.now(tz=timezone.utc),
        matter_ref=raw.get("matter_id"),
        payload={k: v for k, v in raw.items() if k not in ("id", "type", "event_type")},
    )


# ---------------------------------------------------------------------------
# Prometheus
# ---------------------------------------------------------------------------

_webhook_counter: Any = None


def _emit_metric(connector: str, decision: str) -> None:
    global _webhook_counter
    try:
        from prometheus_client import Counter

        if _webhook_counter is None:
            _webhook_counter = Counter(
                "webhook_received_total",
                "Total inbound webhook events by decision.",
                ["connector", "decision"],
            )
        _webhook_counter.labels(connector=connector, decision=decision).inc()
    except Exception as _exc:
        import structlog as _sl
        _sl.get_logger(__name__).debug("metric_init_failed", error=str(_exc))
