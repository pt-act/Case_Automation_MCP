# ADR 001 — Audit log canonical serialisation: sorted-key JSON v1

**Date:** 2026-05-31
**Status:** accepted

## Context

The hash-chained audit log requires a deterministic, byte-stable serialisation
of each record to compute `record_hash = SHA-256(prev_hash || canonical(record))`.
The spec (platform-foundation §5.2, §13.4) flags this as a choice that must be
fixed and version-tagged before go-live — changing it after records exist would
break the chain.

## Decision

Use **sorted-key JSON with no whitespace** (`json.dumps(record, sort_keys=True,
separators=(",", ":"), default=str)`) as the canonical form, tagged as
`canonical_version = "v1"`.  The `canonical_version` field is included in every
record body before hashing, so a future migration to a different encoding
(e.g., length-prefixed encoding, CBOR) can be detected and handled per-record.

## Consequences

- All records written with `canonical_version = "v1"` use this encoding.
- The `AuditService` must never change the serialisation logic for v1 records.
- A future encoding change requires a new `canonical_version` value and a
  migration path for `verify()` to handle mixed-version chains.
- `json.dumps(..., default=str)` converts non-serialisable types (e.g., `datetime`)
  to strings; callers must pass pre-serialised values where precision matters.

## Alternatives considered

- **Length-prefixed encoding** — more robust but requires a custom parser; adds
  complexity for marginal gain at this scale.
- **CBOR** — compact and deterministic but adds a binary dependency and makes
  logs harder to inspect without tooling.
