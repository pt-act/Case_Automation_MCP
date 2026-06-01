# CONNECTOR.md — [Vendor Name] ([Category]: case | crm | email | docstore)

> This file is **required** for every adapter directory.  CI fails if it is
> missing or if any section marked `[required]` is absent or empty.

---

## Auth model [required]

- Auth scheme: OAuth 2.0 | API key | Basic | ...
- Token endpoint: `https://...`
- Scopes required: `scope_1`, `scope_2`
- Token storage: encrypted at column level via `EncryptedStr` (platform-foundation)
- Refresh strategy: ...

---

## Scopes [required]

| Scope | Purpose |
|-------|---------|
| `scope_1` | Read matters |
| `scope_2` | Create contacts |

---

## Key endpoints [required]

| Operation | Method | Path |
|-----------|--------|------|
| Get matter | `GET` | `/v1/matters/{id}` |
| Create matter | `POST` | `/v1/matters` |
| ... | | |

---

## Webhook events [required]

| Event type (vendor) | Normalised type | Notes |
|---------------------|-----------------|-------|
| `matter.updated` | `matter.status_changed` | Contains `status` field |
| ... | | |

**Signature scheme:** `X-Hub-Signature-256: sha256=<hmac-hex>` (or vendor-specific)
**Timestamp header:** `X-Timestamp: <unix-seconds>` (replay window: 5 min)

---

## Rate limits [required]

| Tier | Limit | Window |
|------|-------|--------|
| Default | 100 req | 60s |
| Burst | 20 req | 1s |

`Retry-After` header: yes / no
Idempotency mechanism: `Idempotency-Key` header / vendor field / none

---

## Quirks & known issues

- 409 on idempotent replay: treated as success (returns existing resource). **ASSUMPTION (confirm)**
- Date format: ISO-8601 UTC only
- Pagination: cursor-based; `next_cursor` in response body

---

## Contract test fixtures

Fixtures stored at `tests/connectors/fixtures/<connector-slug>/`.
Record with `respx` or `vcrpy` against a sandbox environment.

---

## How to add this connector

1. Implement the port Protocol in `src/cam/connectors/<vendor>/adapter.py`.
2. Add a normaliser for each webhook event type.
3. Register via `register_connector("<name>", case=adapter, normaliser=normaliser)`.
4. Pass the contract-test harness: `run_contract(case=adapter)`.
5. Complete this `CONNECTOR.md`.
6. No merge without all five steps done (CI enforces).
