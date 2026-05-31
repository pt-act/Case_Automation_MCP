# Security Audit Prep — QC Verification

> Slug: `qc-verification` · Wave 3. Re-checks this feature's surfaces against the
> CONVENTIONS §7 / PTD §12 baseline (the floor, not the ceiling). QC is a
> **safety control**: its correctness *is* a security property, and bypassing or
> degrading it is a security event, not merely a defect. Threats are mapped to
> NFR-1 (confidentiality/privilege), NFR-2 (auditability), NFR-4 (human
> control), NFR-6 (residency/compliance), NFR-8 (secret hygiene).

## Sensitive surfaces (data, actions, external calls)

| Surface | What it touches | Sensitivity |
|---|---|---|
| **`qc.verify` input (`VerificationPacket`)** | Full matter, documents (incl. `privileged`), communications, recipients, extracted fields, immigration PII (names, DOB, A-numbers, passport/receipt numbers) | **High** — concentrates the most sensitive packet data in one call |
| **Privilege check** | Decides whether privileged docs are leaving the firm | **Critical** — a wrong `pass` is a privilege breach |
| **Recipient-integrity check** | Validates external recipients vs participants | **Critical** — guards mis-sent client data |
| **`QCReport` (reasons + evidence)** | Could leak PII if not redacted | **High** — written to audit + run record |
| **Audit record per call** | Hash-chained, immutable, exportable | **High** — integrity & non-repudiation |
| **Aggregation / gate verdict** | The `block` signal a workflow trusts | **Critical** — a wrong aggregate lets bad content through |
| **Check registry (FR-16 plugin point)** | New code path into a safety control | **High** — supply-chain / shadowing risk |
| **`qc_checklist` prompt** | Template surfaced to the agent | **Low/Med** — must carry no client content |
| **External calls** | **None** — QC performs no connector/vendor I/O | n/a — reduced blast radius |

## Threats & mitigations (map to NFR-1, 2, 4, 6, 8)

| # | Threat | Impact | Mitigation | NFR |
|---|---|---|---|---|
| T1 | Privilege check wrongly `pass`es a privileged-doc-to-external packet | Privilege breach (client + ethics) | Fail-closed design; privilege may emit only `pass`/`fail` (never `warn`/`skip`); stricter-of-flags external derivation; dedicated PBT P6; manual review of the check on every code change | NFR-1 |
| T2 | A `fail` is downgraded to `warn`/`pass` (mis-config or rogue check) | Bad content reaches client/gov | Severity policy enforced by registry (out-of-policy verdict rejected, PBT P8); aggregation cannot be overridden inside QC; any-fail-blocks PBT P1 | NFR-1, NFR-4 |
| T3 | QC bypassed at the gate (run proceeds without a verdict) | Unverified external action | Bypass is logged as `qc.bypass_attempt` and alerted; gate contract in `workflow-orchestration` requires a QC verdict; only an **audited** attorney override may proceed past `block` | NFR-2, NFR-4 |
| T4 | PII leaks into audit log / traces / reasons | Confidentiality + residency breach | Reasons/evidence carry field names, checksums, booleans, last-4/redacted ids — never document bodies or raw PII; PII-redaction utility from `platform-foundation`; "no client content in telemetry" test | NFR-1, NFR-6 |
| T5 | Malicious/buggy plugin check shadows or weakens a core check | Safety control silently degraded | Duplicate check-id → startup error (no shadowing); new safety checks reviewed before deploy; descriptor + docs CI-required; checks run under the constrained service identity | NFR-1, NFR-2 |
| T6 | Audit record missing or mutable | Loss of non-repudiation | Exactly-one audit record per call written **before** return (PBT P10); hash-chained append-only table (PTD §12); export verified | NFR-2 |
| T7 | Non-determinism makes a verdict unreproducible | Disputed/var verdicts; audit gaps | `packet.now` injected; no wall-clock in checks; `config_fingerprint` stored with report; determinism PBT P4/P9 | NFR-2 |
| T8 | Check error crashes the run or yields a soft `pass` | Verification skipped silently | Per-check exception + timeout guard → `fail` (fail-closed, PBT P7); totality ensures no silent omission (PBT P3) | NFR-4 |
| T9 | Residency: packet PII processed in a disallowed region | Compliance breach | QC is compute-only, runs in-region with the rest of the host; no external calls; residency inherited from deployment (CONVENTIONS §4, §7) | NFR-6 |
| T10 | Secret exposure | Credential leak | QC holds **no** secrets and reads none; no env-dumping; nothing to leak | NFR-8 |
| T11 | Tampered/oversized packet (DoS or smuggled state) | Latency blowout / injection | Pydantic validation rejects malformed packets; per-check timeout; packet size bound (ASSUMPTION confirm ≤25 docs) | NFR-4, NFR-6 |

## AuthZ & privilege checks

- **Identity:** `qc.verify` runs under the **constrained service identity**
  (PTD §12); it reads only the packet handed to it — no broad data access, no
  ability to fetch other matters. This minimises blast radius if the agent is
  compromised.
- **Least privilege:** QC requires no connector scopes and no secret-store
  access (it makes no external calls).
- **Privilege correctness** is the feature's core AuthZ assertion: privileged
  documents must not be routed externally. Enforced by the `privilege` check
  (fail-closed, never `warn`/`skip`) and verified by PBT P6 and dedicated
  focused tests. Ambiguous `external_bound` is resolved to the **stricter**
  (external) interpretation.
- **No verdict override inside QC:** QC emits an immutable verdict. Proceeding
  past a `block` is only possible via an audited override in
  `workflow-orchestration`, which is its own gated, logged event (NFR-4).

## Audit log coverage

- **Every** `qc.verify` call writes one hash-chained record: `actor`,
  `action="qc.verify"`, `inputs` (packet fingerprint, selected checks,
  `config_fingerprint`), `outputs` (aggregate + per-check verdict/reason,
  redacted), `run_id`, `timestamp` — before the result is returned (NFR-2,
  QR-13).
- **`qc.bypass_attempt`** and any **override** of a `block` are separately
  audited and alerted.
- **Registry changes** (a check added/version-bumped) are recorded at
  deploy-time via the docs/CI + ADR discipline (PTD §16), so the set of active
  safety checks is always reconstructable.
- Records contain **no raw PII** (T4); a test asserts redaction on representative
  immigration PII.

## PII handling & residency

- **Immigration PII in scope:** names, DOB, A-numbers, passport/receipt numbers,
  status, country-of-origin — present in packets (CONVENTIONS §7).
- **In transit/at rest:** TLS + AES-256 inherited from the platform; QC adds no
  new storage of raw PII (it writes only redacted reasons/evidence).
- **In telemetry:** logs/traces are PII-scrubbed; reasons reference field
  *names* and redacted identifiers, never values.
- **Residency:** QC is compute-only and runs in-region with the host; configurable
  data residency is inherited from the deployment (NFR-6, CONVENTIONS §4). No
  data egress from QC.
- **Retention:** `QCReport` lives on the run record and audit log; retention/
  purge policy is inherited from `platform-foundation`. *ASSUMPTION (confirm):
  QC reports follow the same retention schedule as other run artefacts.*

## Pre-audit checklist

- [ ] Privilege check has **no code path** that returns `pass`/`warn`/`skipped`
      when a privileged doc is in an external-bound (or ambiguous) packet (T1,
      PBT P6).
- [ ] Severity policy is registry-enforced; no check can emit a verdict outside
      its declared policy (T2, PBT P8).
- [ ] Any single `fail` yields aggregate `block`; verified by PBT P1 + truth
      table (T2).
- [ ] Every selected check is accounted for (result or `skipped`+reason); no
      silent omission/duplication (T8, PBT P3).
- [ ] Errored/timed-out checks fail closed; the run never crashes or soft-passes
      (T8, PBT P7).
- [ ] Exactly one audit record per `qc.verify`, written before return, hash-
      chained, no raw PII (T4/T6, PBT P10).
- [ ] `qc.bypass_attempt` and `block`-override events are logged and alerted
      (T3).
- [ ] Reasons/evidence/logs contain no client content or raw immigration PII;
      redaction test passes on a PII-laden fixture (T4).
- [ ] Determinism: same packet + config ⇒ same report; `packet.now` injected; no
      wall-clock reads in checks; `config_fingerprint` stored (T7, PBT P4/P9).
- [ ] Duplicate check-id registration fails startup; new checks reviewed +
      documented before deploy (T5).
- [ ] QC holds no secrets and makes no external calls; service identity is
      least-privilege (T10).
- [ ] Malformed/oversized packets rejected by validation + size bound + per-check
      timeout (T11).
- [ ] All `ASSUMPTION (confirm)` items (severity defaults, thresholds, DOB field,
      deadline horizon, packet-kind list, retention) are listed for the firm and
      not silently baked in.
