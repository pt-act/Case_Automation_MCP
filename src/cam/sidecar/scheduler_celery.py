"""CeleryScheduler — production ``Scheduler`` port backed by Celery beat.

Satisfies the ``Scheduler`` protocol defined in
``cam.core.services.deadline.schedule`` (spec §4.1, G4).

The scheduler arms reminders by dispatching ``task_fire_reminder`` with an ETA
and cancels them by revoking the Celery task.  The ``heartbeat()`` method
returns ``datetime.now(UTC)`` because Celery workers keep themselves alive;
the dead-man monitor (``DeadmanMonitor``) checks this value against its
staleness threshold.

Protocol compliance is verified at runtime via ``isinstance(sched, Scheduler)``
thanks to ``@runtime_checkable``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog

log = structlog.get_logger(__name__)


class CeleryScheduler:
    """Production Scheduler backed by Celery beat.

    Implements the ``Scheduler`` protocol from
    ``cam.core.services.deadline.schedule``.

    Each ``arm()`` call enqueues a ``task_fire_reminder`` task with a Celery
    ``eta`` set to ``fire_at``.  The returned ``arm_id`` is the Celery task ID,
    which can be passed to ``cancel()`` to revoke the task before it fires.
    """

    # ------------------------------------------------------------------
    # Scheduler protocol
    # ------------------------------------------------------------------

    async def arm(self, fire_at: datetime, job_ref: str, idem_key: str) -> str:
        """Schedule a reminder task to fire at ``fire_at``.

        Args:
            fire_at:  UTC datetime when the task should fire.
            job_ref:  Opaque reference identifying the job (e.g.
                      ``"deadline_reminder:<idem_key>"``).  Used only for
                      logging; not passed to the task.
            idem_key: Idempotency key forwarded to ``task_fire_reminder`` so
                      that duplicate task executions produce at most one
                      notification.

        Returns:
            arm_id — the Celery async result ID (UUID string).  Store this to
            cancel the task later.
        """
        # Import here to avoid circular imports at module load time
        from cam.sidecar.celery_app import task_fire_reminder

        # Derive deadline_id from job_ref if encoded as "deadline_reminder:<id>"
        # Otherwise use idem_key as a fallback identifier.
        parts = job_ref.split(":", 1)
        deadline_id = parts[1] if len(parts) == 2 else idem_key

        result = task_fire_reminder.apply_async(
            kwargs={"deadline_id": deadline_id, "reminder_idem_key": idem_key},
            eta=fire_at,
            task_id=str(uuid.uuid4()),
        )

        arm_id: str = result.id
        log.info(
            "celery_scheduler.armed",
            arm_id=arm_id,
            job_ref=job_ref,
            # Log fire_at as ISO string only — no raw datetime object in structlog JSON
            fire_at=fire_at.isoformat(),
        )
        return arm_id

    async def cancel(self, arm_id: str) -> None:
        """Revoke a previously armed task.

        Revocation is best-effort: if the task has already fired, the revoke
        call is a no-op (Celery will ignore the revocation of a completed
        task).

        Args:
            arm_id: The Celery task ID returned by ``arm()``.
        """
        from cam.sidecar.celery_app import app as celery_app

        try:
            celery_app.control.revoke(arm_id, terminate=False)
            log.info("celery_scheduler.cancelled", arm_id=arm_id)
        except Exception as exc:
            # Revocation errors must never propagate and silently kill a caller
            # (e.g. mark_done).  Log and swallow.
            log.warning("celery_scheduler.cancel_error", arm_id=arm_id, error=str(exc))

    async def heartbeat(self) -> datetime:
        """Return the current UTC time.

        Celery workers self-maintain; as long as this method is callable the
        scheduler is live.  The ``DeadmanMonitor`` compares the returned
        timestamp against its staleness threshold.

        Returns:
            Current UTC datetime (timezone-aware).
        """
        now = datetime.now(tz=timezone.utc)
        log.debug("celery_scheduler.heartbeat", ts=now.isoformat())
        return now
