# PACK.md — Consulting / Advisory (proof pack)

**Pack id:** `consulting` · **Version:** `0.1.0` · **Status:** reference pack (proof of generality)

A minimal, self-contained consulting/advisory practice. It shares no values with
the immigration pack. Its purpose is to prove the seam generalises: the same
engine runs intake → QC → deadline → routing with different vocabulary, case
types, document classes, and confidentiality label — with **no core edits**.

## Terminology

| Slot | Label |
|---|---|
| matter | Engagement |
| contact | Client |
| deadline | Key Date |
| restriction_label | **Client-Confidential** |
| practice_noun | service line |

## Confidentiality (restriction) semantics

- Label: **Client-Confidential**. `default_restricted = True` (fail-safe).
- Same engine-owned, fail-closed, never-warn mechanics as every pack — only the
  label differs from the immigration "Privileged".

## PII additions

None. The pack inherits the full engine PII floor (email, phone, SSN/ITIN, DOB)
and adds nothing.

## Case types

`advisory` (default), `audit`. Required fields, opening tasks, and welcome
templates defined locally in `pack.py`.

## Deadline rules

Illustrative: `deliverable_due` (advisory deliverable, 30 business days from
engagement start). `assumption_unconfirmed=true`.

## RBAC roles

`partner`, `consultant`, `operations`, `agent_service` (constrained AI identity),
`admin`. Defined locally as permission-string sets.

## Provenance / assumptions

Entirely illustrative; encoded to demonstrate the contract, not a real firm's
rules. All contents are `ASSUMPTION (confirm)`.
