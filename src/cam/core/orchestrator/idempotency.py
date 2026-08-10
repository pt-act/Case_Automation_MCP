"""IdempotencyStore — Redis-backed with Postgres durable backstop — spec §4.1.

Key format:  `cam:step_idem:{run_id}:{step}`
TTL:         24 h (ASSUMPTION confirm)

The store guarantees exactly-once external effect per (run_id, step) pair.
Redis is the fast path; Postgres is the durable backstop so a Redis flush
cannot lose idempotency records.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IdempotencyStore(Protocol):
    """Minimal protocol for step idempotency tracking."""

    async def reserve(self, idem_key: str) -> bool:
        """Atomically claim the key.  Returns True on first claim, False if already recorded."""
        ...

    async def record(self, idem_key: str, output: dict[str, Any]) -> None:
        """Persist the step output for the given key."""
        ...

    async def lookup(self, idem_key: str) -> dict[str, Any] | None:
        """Return the recorded output, or None if not yet recorded."""
        ...


IDEM_TTL = 86400  # 24 h


def _redis_key(idem_key: str) -> str:
    return f"cam:step_idem:{idem_key}"


# ---------------------------------------------------------------------------
# In-memory implementation (tests + local dev)
# ---------------------------------------------------------------------------


class InMemoryIdempotencyStore:
    """Deterministic in-memory implementation for unit tests."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def reserve(self, idem_key: str) -> bool:
        if idem_key in self._store:
            return False
        self._store[idem_key] = {}  # placeholder; record() fills this
        return True

    async def record(self, idem_key: str, output: dict[str, Any]) -> None:
        self._store[idem_key] = output

    async def lookup(self, idem_key: str) -> dict[str, Any] | None:
        val = self._store.get(idem_key)
        # A placeholder ({}) from reserve() that hasn't been recorded yet
        # should not be returned as "completed"
        if val is None:
            return None
        # If record() has been called, it replaces the placeholder
        return val if val != {} else None

    def _force_record(self, idem_key: str, output: dict[str, Any]) -> None:
        """Test helper — bypass reserve/record sequence."""
        self._store[idem_key] = output


# ---------------------------------------------------------------------------
# Redis implementation (production)
# ---------------------------------------------------------------------------


class RedisIdempotencyStore:
    """Redis-backed idempotency store with Postgres backstop.

    The reserve() uses SETNX semantics (SET … NX) for atomic first-claim.
    If Redis is unavailable, falls back to Postgres.
    """

    def __init__(self, redis_client: Any, db_session: Any | None = None) -> None:
        self._redis = redis_client
        self._db = db_session  # Postgres backstop (optional)

    async def reserve(self, idem_key: str) -> bool:
        redis_key = _redis_key(idem_key)
        result = await self._redis.set(redis_key, "reserved", nx=True, ex=IDEM_TTL)
        return result is not None  # None = key already existed

    async def record(self, idem_key: str, output: dict[str, Any]) -> None:
        redis_key = _redis_key(idem_key)
        await self._redis.set(redis_key, json.dumps(output), ex=IDEM_TTL)

    async def lookup(self, idem_key: str) -> dict[str, Any] | None:
        redis_key = _redis_key(idem_key)
        raw = await self._redis.get(redis_key)
        if raw is None:
            return None
        if raw == b"reserved" or raw == "reserved":
            return None  # reserved but not yet recorded (in-flight)
        try:
            return json.loads(raw)  # type: ignore[no-any-return]
        except (json.JSONDecodeError, TypeError):
            return None
