# PACK.md — Immigration (reference pack)

**Pack id:** `immigration` · **Version:** `1.0.0` · **Status:** reference pack

The flagship reference pack for a US immigration law practice. With this pack
active (`CAM_DOMAIN_PACK=immigration`) the engine reproduces its original
behaviour 1:1 — the existing test suite passes unchanged in outcome.

## Terminology

| Slot | Label |
|---|---|
| matter | Matter |
| contact | Client |
| deadline | Deadline |
| restriction_label | **Privileged** (attorney–client privilege) |
| practice_noun | practice area |

## Confidentiality (restriction) semantics

- Label: **Privileged**. `default_restricted = True` (fail-safe).
- Engine-owned mechanics (a pack cannot weaken them): a restricted document in an
  external-bound packet **fails**, never warns; the check takes the stricter of
  the caller claim vs the re-derived external-bound. No extra predicates — behaves
  identically to the original `privilege` check.

## PII additions (on top of the engine baseline floor)

| Pattern | Matches |
|---|---|
| `\bA\d{8,9}\b` | A-number |
| `\b[A-Z]{1,2}\d{6,9}\b` | Passport (simplified) |

The engine baseline (email, phone, SSN/ITIN, DOB) is inherited and non-removable.

## Case types

`family-based`, `employment-based`, `other/uncategorised` (default). Defined in
this pack (`pack.py`); the engine reads them from the active pack at startup via
`apply_pack()`. Required fields, opening-task checklists, and welcome templates
are pack-owned — core intake holds only a neutral fallback case type.

## Identifiers & forms

- **`identifier_patterns`**: `{"a_number": ^A\d{8,9}$}` — the intake A-number
  field validator reads this from the active pack (core hard-codes no format).
- **`prefill_forms`**: `I-130 → i130_petition`, `I-485 → i485_adjustment`,
  `N-400 → n400_naturalization`, `G-28 → g28_representation` — `form.prefill`
  resolves a form id to a template id via the active pack.

## Deadline rules

Illustrative only, all `assumption_unconfirmed=true` (per `ASSUMPTIONS.md`
DE-001): `rfe_response` (RFE response window). Real rule contents are confirmed
with the firm before production.

## RBAC roles

`attorney`, `paralegal`, `intake_coordinator`, `operations`, `agent_service`
(constrained AI identity), `admin` — projected from the engine's default-deny
permission matrix in `security/rbac.py`. The role/permission matrix is
engine-neutral structure (no immigration value), so it is owned by the engine and
projected here rather than redefined.

## Provenance / assumptions

The immigration domain values (case types, the a_number format, prefill form ids,
the "Privileged" label, PII additions) live in this pack; the engine core holds
none of them and reads them from the active pack (G6 complete — enforced by the
`test_no_immigration_value_hardcoded_in_core` guard). All case types, forms, and
deadline contents remain `ASSUMPTION (confirm)` per `docs/ASSUMPTIONS.md`.
