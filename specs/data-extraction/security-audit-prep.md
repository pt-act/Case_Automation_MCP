# Security Audit Prep — Data Extraction

> Inherits `_shared/CONVENTIONS.md §7` (security/compliance/audit baseline — the
> floor, not the ceiling). Re-checks this feature's own surfaces. Slug:
> `data-extraction`. Primary controls: **inference residency (NFR-6, NFR-1)** and
> **no client content in telemetry**.

## Sensitive surfaces (data, actions, external calls)

| # | Surface | Why sensitive |
|---|---|---|
| S1 | **Raw document content / inbound email body** (PDF/image bytes, OCR text) | May be privileged + immigration PII (A-numbers, passports, biometrics, status, country-of-origin). |
| S2 | **Outbound call to the LLM inference provider** | Sends client text outside the process; possibly outside the firm boundary. Central residency risk. |
| S3 | **Provider API keys / credentials** | Secret material; must never be logged or in source. |
| S4 | **Logs, traces, metrics (telemetry)** | Could leak raw content / PII / field values if unscrubbed. |
| S5 | **Extracted field values + confidence (ExtractionProposal)** | Structured PII returned to the caller. |
| S6 | **Run records / audit entries** | Must contain metadata only — no content, no values. |
| S7 | **Async staging of intermediate content** | Transient at-rest content if OCR/LLM runs async. |
| S8 | **Mapping profiles / `extraction_schema` prompt config** | Define what is extracted; tampering could over-collect PII. |
| S9 | **`document.extract` tool input** | Untrusted `input_ref`, `threshold`, `mapping_profile` from the agent/caller. |

## Threats & mitigations (map to NFR-1, 2, 4, 6, 8)

| Threat | NFR | Mitigation |
|---|---|---|
| Client content sent to a **disallowed / out-of-region inference endpoint** | NFR-6, NFR-1 | `ResidencyGuard` checks endpoint/region against allow-list **before** any call; **fail-closed**; `EXTRACTION_ALLOW_EXTERNAL_INFERENCE=false` by default; residency denial logged as a security event with no content. |
| **Raw content or PII leaks into logs/traces/metrics** | NFR-1 | PII-scrubbing log processors; telemetry records counts/decisions only; redaction property test; no field values in spans. |
| **Field values / content persisted** in run or audit records | NFR-1, NFR-2 | Run record + audit are **metadata-only** by schema (no value columns); enforced by test asserting absence of content. |
| **Provider key disclosure** | NFR-8 | Keys read from central secret store at runtime; never in source; never logged; no env-dumping. |
| Extraction silently **writes to a system of record** | NFR-4 | Tool risk tier `read`; architecturally no write capability; PBT P3 (`SpyConnectors().write_calls == []`). |
| **Low-confidence value accepted** into a system of record downstream | NFR-4 | `requires_verification` always set for below-threshold/rule-forced fields (PBT P2); persistence is the consumer's gated action — extraction never clears the gate. |
| **Tampered mapping profile / prompt** over-collects or mis-targets PII | NFR-1 | Profiles/prompt versioned + reviewed; loaded from trusted config; changes audited. |
| **Malicious/oversized input** (DoS, decompression) | NFR-9 | Size/page limits (`InputLimitError`); per-stage timeouts; graceful degradation if provider down. |
| **Untrusted tool inputs** (`threshold`, `input_ref`) | NFR-1 | Strict Pydantic validation; `threshold` constrained to `[0,1]`; `input_ref` resolved through authorised access only. |
| Intermediate content lingers in **async staging** | NFR-1 | Encrypt at rest; short TTL; purge on completion (`ASSUMPTION (confirm)` retention window). |

## AuthZ & privilege checks

- `document.extract` runs under the **constrained service identity** (CONVENTIONS
  §7); RBAC scopes which roles may invoke it.
- The caller must be authorised to access `input_ref`; extraction does not widen
  access to documents the caller could not otherwise read.
- The tool holds **no write scope** to any system of record (least privilege by
  construction) — it cannot persist, only propose.
- **Privilege preservation:** extracting from a privileged document does not
  declassify it; the proposal carries no authority to route/send. Privilege-aware
  routing is enforced downstream (`document-routing` / `qc-verification`).
- Residency policy (`ALLOW_EXTERNAL_INFERENCE`, allow-list) is config-gated and
  not overridable from tool input.

## Audit log coverage

- **One metadata audit record per run** (hash-chained, per CONVENTIONS §7):
  `actor, action="document.extract", input_ref, input_checksum, provider,
  threshold, field_count, low_conf_count, structuring_status, deterministic,
  timestamp, run_id`.
- **Excluded from audit:** raw content, OCR/extracted text, field values, PII.
- **Security events** additionally audited: residency denial, input-limit
  rejection, provider-unavailable degradation.
- Audit chain integrity is covered by `platform-foundation` PBTs; this feature
  asserts its records are written **before** the run is considered complete and
  contain metadata only.

## PII handling & residency

- Immigration PII classes in scope: A-numbers, passport/travel-doc numbers,
  biometrics references, immigration status, country-of-origin, dates of birth.
- **In transit:** TLS; **at rest:** AES-256 for any staged content; OAuth/provider
  tokens encrypted (baseline).
- **In telemetry:** none — scrubbed; counts/decisions only.
- **Residency:** inference endpoint constrained to the configured allow-list;
  external inference disabled by default; whether client data may leave the firm
  boundary is an explicit per-deployment decision (`ASSUMPTION (confirm)`).
- **Data minimisation:** only fields declared by the active mapping profile are
  extracted/returned; unknown fields are left absent (not guessed).

## Pre-audit checklist

- [ ] Residency allow-list configured; `EXTRACTION_ALLOW_EXTERNAL_INFERENCE`
      reviewed and confirmed per deployment.
- [ ] Negative test proves a non-allow-listed endpoint **fails closed with no
      outbound content call**.
- [ ] Redaction test proves no raw content / PII / field values in logs, traces,
      or metrics.
- [ ] Run records + audit entries verified metadata-only (no values/content).
- [ ] PBT P3 green: zero system-of-record writes from `document.extract`.
- [ ] PBT P2 green: every below-threshold / rule-forced field flagged.
- [ ] Provider keys confirmed sourced from secret store; absent from source and
      logs; no env-dumping commands in code/scripts.
- [ ] Input size/page limits and per-stage timeouts enforced; oversized input
      rejected.
- [ ] Async staging (if used) encrypted with confirmed TTL/purge.
- [ ] Mapping profiles + `extraction_schema` prompt are versioned, reviewed, and
      change-audited.
- [ ] RBAC scopes for `document.extract` reviewed; service identity holds no
      write scope.
- [ ] Open `ASSUMPTION (confirm)` items (provider, boundary policy, threshold,
      retention) tracked and resolved before go-live.
