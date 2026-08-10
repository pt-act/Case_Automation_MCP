"""Outbound HTTP client middleware — spec §4.3.

All adapters call `OutboundClient.request()` instead of raw httpx so that:
  - retry / backoff / jitter are applied consistently (tenacity)
  - rate-limit Retry-After is honoured
  - idempotency is enforced via Redis
  - the circuit breaker gates every call
  - spans, metrics, and audit records are emitted uniformly
  - no request/response body, PII, or token ever appears in logs
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from cam.connectors.errors import (
    ConnectorError,
    FatalError,
    RateLimitError,
    classify,
)
from cam.connectors.health import ConnectorHealth, get_health

# ---------------------------------------------------------------------------
# RetryPolicy — configurable per call
# ---------------------------------------------------------------------------


@dataclass
class RetryPolicy:
    """Retry bounds for a single OutboundClient call."""

    max_attempts: int = 5
    base_delay: float = 0.5
    max_delay: float = 30.0
    total_deadline: float = 120.0


DEFAULT_RETRY = RetryPolicy()


# ---------------------------------------------------------------------------
# IdempotencyRecord (Redis-backed)
# ---------------------------------------------------------------------------


def _idem_redis_key(connector: str, idem_key: str) -> str:
    return f"cam:idem:{connector}:{idem_key}"


async def _idem_get(redis_client: Any, connector: str, idem_key: str) -> dict[str, Any] | None:
    raw = await redis_client.get(_idem_redis_key(connector, idem_key))
    if raw is None:
        return None
    return json.loads(raw)  # type: ignore[no-any-return]


async def _idem_set(
    redis_client: Any, connector: str, idem_key: str, result: Any, ttl: int = 86400
) -> None:
    await redis_client.set(
        _idem_redis_key(connector, idem_key),
        json.dumps({"result": result, "status": "completed"}),
        ex=ttl,
    )


def _derive_idem_key(connector: str, operation: str, logical_args: str) -> str:
    """Derive a deterministic idempotency key from logical arguments.
    Uses uuid5 to ensure the same logical write always maps to the same key.
    """
    namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # URL namespace
    return str(uuid.uuid5(namespace, f"{connector}:{operation}:{logical_args}"))


# ---------------------------------------------------------------------------
# OutboundClient
# ---------------------------------------------------------------------------


class OutboundClient:
    """Shared async HTTP client wrapper for all connector adapters.

    Usage::

        client = OutboundClient("case", httpx_client, redis_client)
        response = await client.request("GET", "https://api.vendor.com/matters/1")
    """

    def __init__(
        self,
        connector: str,
        http_client: httpx.AsyncClient,
        redis_client: Any | None = None,
        classify_fn: Callable[[Any, str], ConnectorError] = classify,
        failure_threshold: int = 5,
        cool_down_seconds: float = 60.0,
    ) -> None:
        self.connector = connector
        self._http = http_client
        self._redis = redis_client
        self._classify = classify_fn
        self._health: ConnectorHealth = get_health(
            connector,
            failure_threshold=failure_threshold,
            cool_down_seconds=cool_down_seconds,
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        idem_key: str | None = None,
        retry: RetryPolicy = DEFAULT_RETRY,
        timeout: float = 10.0,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        is_write: bool = False,
        operation: str = "",
    ) -> httpx.Response:
        """Execute an HTTP request with circuit-breaker, retry, and idempotency.

        Thin coordinator — each concern is handled by a dedicated helper:
          _guard_circuit        → fail fast if circuit is open
          _resolve_idem_key     → derive / look up idempotency key for writes
          _execute_with_retry   → retry loop with backoff + deadline
          _record_success       → update health + store idempotency result
        """
        self._guard_circuit()
        effective_idem_key = await self._resolve_idem_key(
            method, url, json_body, idem_key, is_write, operation
        )
        if effective_idem_key is not None and is_write:
            cached = await _idem_get(self._redis, self.connector, effective_idem_key)
            if cached and cached.get("status") == "completed":
                _emit_metric(self.connector, operation, "idempotent_replay")
                return httpx.Response(200, json=cached["result"])

        response = await self._execute_with_retry(
            method, url, headers=headers, json_body=json_body, timeout=timeout, retry=retry
        )
        await self._record_success(response, is_write, effective_idem_key, operation)
        return response

    # ------------------------------------------------------------------
    # Helpers (each handles one concern, individually testable)
    # ------------------------------------------------------------------

    def _guard_circuit(self) -> None:
        """Raise immediately if the circuit breaker is open (fail-fast)."""
        if self._health.is_open:
            raise self._health.last_error or FatalError(
                self.connector, "Circuit breaker is open."
            )

    async def _resolve_idem_key(
        self,
        method: str,
        url: str,
        json_body: Any,
        idem_key: str | None,
        is_write: bool,
        operation: str,
    ) -> str | None:
        """Return the effective idempotency key for a write, or None for reads."""
        if not is_write or self._redis is None:
            return None
        if idem_key is not None:
            return idem_key
        logical = f"{method}:{url}:{json.dumps(json_body, sort_keys=True, default=str)}"
        return _derive_idem_key(self.connector, operation, logical)

    async def _execute_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None,
        json_body: Any,
        timeout: float,
        retry: RetryPolicy,
    ) -> httpx.Response:
        """Retry loop: exponential jitter backoff up to total_deadline."""
        deadline = time.monotonic() + retry.total_deadline
        attempt = 0
        while True:
            attempt += 1
            if time.monotonic() >= deadline:
                err = FatalError(self.connector, "Total deadline exceeded.")
                self._health.record_failure(err)
                raise err
            try:
                return await self._single_attempt(
                    method, url, headers=headers, json_body=json_body, timeout=timeout
                )
            except ConnectorError as exc:
                self._health.record_failure(exc)
                if not exc.retryable or attempt >= retry.max_attempts:
                    if exc.retryable and attempt >= retry.max_attempts:
                        raise FatalError(
                            self.connector,
                            f"Retry exhausted after {attempt} attempts.",
                            cause=exc,
                        ) from exc
                    raise
                wait = _jitter_wait(attempt, retry.base_delay, retry.max_delay)
                if isinstance(exc, RateLimitError) and exc.retry_after is not None:
                    wait = max(wait, exc.retry_after)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FatalError(self.connector, "Total deadline exceeded mid-retry.") from exc
                await asyncio.sleep(min(wait, remaining))

    async def _record_success(
        self,
        response: httpx.Response,
        is_write: bool,
        effective_idem_key: str | None,
        operation: str,
    ) -> None:
        """Update circuit health and persist idempotency result on success."""
        self._health.record_success()
        if is_write and self._redis is not None and effective_idem_key:
            try:
                result_data: Any = response.json() if response.content else {}
            except Exception:
                result_data = {}
            await _idem_set(self._redis, self.connector, effective_idem_key, result_data)
        _emit_metric(self.connector, operation, "success")

    async def _single_attempt(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None,
        json_body: Any,
        timeout: float,
    ) -> httpx.Response:
        """One HTTP attempt.  Classifies failures into ConnectorError."""
        import structlog

        log = structlog.get_logger(__name__)
        start = time.monotonic()
        try:
            response = await self._http.request(
                method,
                url,
                headers=headers or {},
                json=json_body,
                timeout=timeout,
            )
            latency_ms = (time.monotonic() - start) * 1000
            log.debug(
                "connector.request",
                connector=self.connector,
                method=method,
                host=_host(url),
                status=response.status_code,
                latency_ms=round(latency_ms, 1),
            )
            _emit_metric(self.connector, "", f"http_{response.status_code}")

            if not response.is_success:
                raise self._classify(response, self.connector)
            return response

        except ConnectorError:
            raise
        except Exception as exc:
            err = self._classify(exc, self.connector)
            log.warning(
                "connector.exception",
                connector=self.connector,
                error_class=type(err).__name__,
            )
            raise err from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jitter_wait(attempt: int, base: float, cap: float) -> float:
    """Full jitter: random.uniform(0, min(cap, base * 2^attempt))."""
    import secrets as _sr
    ceiling = min(cap, base * (2**attempt))
    return _sr.SystemRandom().uniform(0, ceiling)


def _host(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc or url[:40]
    except Exception:
        return url[:40]


_request_counter: Any = None


def _emit_metric(connector: str, operation: str, result: str) -> None:
    global _request_counter
    try:
        from prometheus_client import Counter

        if _request_counter is None:
            _request_counter = Counter(
                "connector_request_total",
                "Total outbound connector requests.",
                ["connector", "operation", "result"],
            )
        _request_counter.labels(
            connector=connector, operation=operation or "unknown", result=result
        ).inc()
    except Exception:
        pass
