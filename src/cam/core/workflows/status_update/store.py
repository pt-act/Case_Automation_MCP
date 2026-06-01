"""Last-known-status store + trigger allow-list config — spec G1.3.

Persists per-matter last-known status for sweep comparison.
Tracks delivered change_ids to enforce the one-send-per-change seal.
Trigger allow-list gates which status values produce a notification.

ASSUMPTION (confirm):
  - Trigger allow-list contents (which statuses to notify on)
  - Per-status suppression policy (reverts, duplicates)
  - Sweep cadence
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StatusUpdateConfig:
    """Configuration for the status-update-emails workflow."""

    trigger_allow_list: list[str] = field(default_factory=list)
    """Status values that trigger a notification.  Default empty → nothing triggers.
    ASSUMPTION (confirm): exact list from firm."""

    suppress_reverts: bool = False
    """If True, a revert (A→B→A) does not re-notify.  ASSUMPTION (confirm)."""


class LastKnownStatusStore:
    """In-memory per-matter status tracking + delivered change_id registry."""

    def __init__(self) -> None:
        self._last_status: dict[str, str] = {}
        self._transition_counters: dict[str, int] = {}
        self._delivered: dict[str, str] = {}  # change_id → run_id

    # ------------------------------------------------------------------
    # Last-known status
    # ------------------------------------------------------------------

    def get_last_status(self, matter_id: str) -> str | None:
        return self._last_status.get(matter_id)

    def update_last_status(self, matter_id: str, status: str) -> None:
        self._last_status[matter_id] = status

    def next_transition_counter(self, matter_id: str) -> int:
        """Return a monotonically increasing counter per matter (sweep discriminator)."""
        n = self._transition_counters.get(matter_id, 0) + 1
        self._transition_counters[matter_id] = n
        return n

    # ------------------------------------------------------------------
    # Delivered seal
    # ------------------------------------------------------------------

    def is_delivered(self, change_id: str) -> bool:
        return change_id in self._delivered

    def mark_delivered(self, change_id: str, run_id: str) -> None:
        self._delivered[change_id] = run_id

    def get_run_for_change(self, change_id: str) -> str | None:
        return self._delivered.get(change_id)

    # ------------------------------------------------------------------
    # Allow-list check
    # ------------------------------------------------------------------

    @staticmethod
    def should_trigger(to_status: str, config: StatusUpdateConfig) -> bool:
        if not config.trigger_allow_list:
            return False
        return to_status in config.trigger_allow_list
