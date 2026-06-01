"""Dead-man's-switch — scheduler liveness monitor — spec §5, G6.

The monitor runs on an INDEPENDENT timer so a stopped scheduler cannot
prevent the monitor from detecting its own failure.  Staleness → safety incident.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)

DEFAULT_STALENESS_THRESHOLD_SECONDS = 300  # 5 minutes ASSUMPTION (confirm)


class DeadmanMonitor:
    """Checks scheduler heartbeat age; fires an alert if stale."""

    def __init__(
        self,
        scheduler: Any,  # Scheduler protocol
        threshold_seconds: float = DEFAULT_STALENESS_THRESHOLD_SECONDS,
        alert_fn: Any | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._threshold = threshold_seconds
        self._alert_fn = alert_fn
        self._running = False

    async def check_once(self) -> bool:
        """Perform one heartbeat check.  Returns True if healthy, False if stale."""
        try:
            last_beat = await self._scheduler.heartbeat()
            age = (datetime.now(tz=timezone.utc) - last_beat).total_seconds()
            if age > self._threshold:
                await self._fire_alert(age)
                return False
            return True
        except Exception as exc:
            log.error("deadman.heartbeat_error", error=str(exc))
            await self._fire_alert(-1)
            return False

    async def _fire_alert(self, age_seconds: float) -> None:
        log.error(
            "deadman.scheduler_stale",
            age_seconds=age_seconds,
            threshold_seconds=self._threshold,
        )
        if self._alert_fn is not None:
            try:
                await self._alert_fn(
                    event="deadline_scheduler_stale",
                    age_seconds=age_seconds,
                    threshold_seconds=self._threshold,
                )
            except Exception:
                pass

    async def run_loop(self, interval_seconds: float = 60.0) -> None:
        """Run the monitor loop indefinitely on an independent cadence."""
        self._running = True
        while self._running:
            await self.check_once()
            await asyncio.sleep(interval_seconds)

    def stop(self) -> None:
        self._running = False
