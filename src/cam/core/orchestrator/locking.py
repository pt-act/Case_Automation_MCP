"""Distributed run locking via Redis SETNX.

Prevents two Celery workers from executing the same workflow run concurrently.
When Redis is unavailable, locking silently degrades to a no-op (single-worker
mode is safe without distributed locking).

Usage::

    from cam.core.orchestrator.locking import RunLock

    lock = RunLock(redis_client)
    if await lock.acquire(run_id):
        try:
            await engine.execute(run_id)
        finally:
            await lock.release(run_id)
    else:
        log.info("run.locked", run_id=run_id)
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#: Default lock TTL in seconds.  If the worker crashes, the lock auto-expires
#: so another worker can pick up the run after this period.
DEFAULT_LOCK_TTL = 300

#: Key prefix for run locks in Redis.
_LOCK_PREFIX = "cam:run_lock:"


class RunLock:
    """Distributed run lock backed by Redis SETNX.

    Args:
        redis_client: An async Redis client (``redis.asyncio.Redis``).
                      If None, locking is a no-op (single-worker mode).
        ttl_seconds: Lock time-to-live.  Auto-expires on worker crash.
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        ttl_seconds: int = DEFAULT_LOCK_TTL,
    ) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    async def acquire(self, run_id: str) -> bool:
        """Try to acquire a lock for ``run_id``.

        Returns True if the lock was acquired, False if another worker holds it.
        When no Redis client is configured, always returns True (no-op).
        """
        if self._redis is None:
            return True  # single-worker mode — no locking needed

        key = f"{_LOCK_PREFIX}{run_id}"
        try:
            result = await self._redis.set(key, "1", nx=True, ex=self._ttl)
            if result:
                log.debug("run.lock.acquired", extra={"run_id": run_id})
                return True
            log.debug("run.lock.held", extra={"run_id": run_id})
            return False
        except Exception as exc:
            # Redis errors are non-fatal — degrade to no locking
            log.warning("run.lock.redis_error", extra={"run_id": run_id, "error": str(exc)})
            return True

    async def release(self, run_id: str) -> None:
        """Release the lock for ``run_id``.

        Safe to call even if the lock was never acquired or already expired.
        """
        if self._redis is None:
            return

        key = f"{_LOCK_PREFIX}{run_id}"
        try:
            await self._redis.delete(key)
            log.debug("run.lock.released", extra={"run_id": run_id})
        except Exception as exc:
            log.warning("run.lock.release_error", extra={"run_id": run_id, "error": str(exc)})

    async def extend(self, run_id: str) -> None:
        """Extend the lock TTL (call during long-running steps).

        Resets the expiry to ``ttl_seconds`` from now.
        """
        if self._redis is None:
            return

        key = f"{_LOCK_PREFIX}{run_id}"
        try:
            await self._redis.expire(key, self._ttl)
        except Exception as exc:
            log.warning("run.lock.extend_error", extra={"run_id": run_id, "error": str(exc)})
