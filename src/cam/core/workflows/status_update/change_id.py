"""Deterministic change_id derivation — spec G1.2.

change_id = sha256(matter_id:from_status:to_status:discriminator)[:16]

Discriminator:
  - webhook: provider_event_id  (already deduped upstream by CF)
  - sweep:   str(transition_counter)  per-matter monotonic int
  - manual:  timestamp string

Identical inputs → identical id (stable).
Distinct transitions → distinct id.
A→B→A revert yields a different id from A→B (discriminator differs).

ASSUMPTION (confirm): exact composition once vendor status semantics are known.
"""

from __future__ import annotations

import hashlib


def derive_change_id(
    matter_id: str,
    from_status: str,
    to_status: str,
    discriminator: str,
) -> str:
    """Return a 16-character hex change_id."""
    raw = f"{matter_id}:{from_status}:{to_status}:{discriminator}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def webhook_change_id(matter_id: str, from_status: str, to_status: str,
    provider_event_id: str) -> str:
    return derive_change_id(matter_id, from_status, to_status, provider_event_id)


def sweep_change_id(matter_id: str, from_status: str, to_status: str,
    transition_counter: int) -> str:
    return derive_change_id(matter_id, from_status, to_status, str(transition_counter))
