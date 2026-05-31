# Tasks — Data Extraction

> Inherits `_shared/CONVENTIONS.md` (§10 status/size/dependency legend). Slug:
> `data-extraction`. Cross-spec deps use `<slug>#<id>`. Sizes: XS/S/M/L.
> Definition of done for every group = **code + tests + docs**.

## Overview (task groups + critical path + parallelisation)

| Group | Name | Theme |
|---|---|---|
| G1 | Feature-local types & config | Pydantic types, config keys, errors |
| G2 | Text extraction stage | pdfplumber/PyMuPDF text layer + coverage |
| G3 | OCR fallback stage | Tesseract per-page fallback + confidence |
| G4 | Page-coverage accounting | union/disjoint guarantees |
| G5 | Provider-agnostic LLM structuring + ResidencyGuard | swappable client, fail-closed |
| G6 | Domain mapping + confidence gating | data-driven map, threshold flags |
| G7 | MCP tool + prompt | `document.extract`, `extraction_schema` |
| G8 | Audit, observability, security hardening | metadata audit, spans, redaction |
| G9 | PBT + docs | properties + feature docs/CI |

**Critical path:** G1 → G2 → G4 → G6 → G7 → G8 → G9. G3 depends on G2; G5
depends on G1 and feeds G6. G3 and G5 are parallelisable once G1/G2 land.

## Group 1: Feature-local types & config

- [ ] **1.1** Define Pydantic v2 types (`ExtractedField`, `ExtractionSource`,
  `PageCoverage`, `ExtractionProposal`, `ExtractInput`, `MappingProfile`).
  — size **S** · depends on: `platform-foundation#domain-model` · parallel: no
  · **Acceptance:** types validate; `confidence` field constrained to `[0,1]`;
  `ExtractInput.threshold` validated to `[0,1]`; JSON round-trips.
  · **Tests:** confidence-bounds validator rejects <0/>1/NaN; threshold
  validator; proposal serialises/deserialises; coverage type holds disjoint sets.
- [ ] **1.2** Define config keys + typed errors (`ResidencyError`,
  `UnsupportedInputError`, `ProviderUnavailableError`, `InputLimitError`) and
  default threshold (`ASSUMPTION (confirm): 0.80`).
  — size **XS** · depends on: `platform-foundation#config-secrets` · parallel: yes
  · **Acceptance:** config loads from env; `EXTRACTION_ALLOW_EXTERNAL_INFERENCE`
  defaults `false`; errors are a typed taxonomy.
  · **Tests:** default config loads; missing provider key surfaces typed error.

## Group 2: Text extraction stage

- [ ] **2.1** Implement `TextExtractor` for native PDFs (pdfplumber/PyMuPDF):
  per-page text + which pages had a usable text layer.
  — size **M** · depends on: 1.1 · parallel: no
  · **Acceptance:** native PDF returns per-page text; pages with no text layer
  reported as needing OCR; deterministic for identical bytes.
  · **Tests:** native PDF → text, no OCR flagged; empty-text page flagged for OCR;
  determinism (same bytes → identical output); email-body path bypasses paging.
- [ ] **2.2** Input validation + format/size guards (`pdf`/`image`/`email_body`,
  max pages/bytes).
  — size **S** · depends on: 1.1, 1.2 · parallel: yes
  · **Acceptance:** unsupported kind → `UnsupportedInputError`; oversized →
  `InputLimitError`; zero-byte → validation error.
  · **Tests:** each rejection path; valid input passes.

## Group 3: OCR fallback stage

- [ ] **3.1** Implement `OcrEngine` (Tesseract) over pages lacking a text layer;
  capture per-page OCR confidence; configurable langs.
  — size **M** · depends on: 2.1 · parallel: yes (with G5)
  · **Acceptance:** image/scanned page → OCR text + confidence; only pages
  without usable text are OCR'd; langs configurable.
  · **Tests:** scanned page → OCR invoked; native page → OCR not invoked; empty
  OCR result handled (warning, no fabricated field); lang config respected.

## Group 4: Page-coverage accounting

- [ ] **4.1** Build `PageCoverage` from text + OCR + skipped sets; enforce
  union==all-pages and pairwise-disjoint; surface `skipped_pages` + warnings.
  — size **S** · depends on: 2.1, 3.1 · parallel: no
  · **Acceptance:** coverage union equals full page set; sets disjoint; corrupt
  page → `skipped` + warning, run still completes.
  · **Tests:** all-text doc; all-ocr doc; mixed doc; one-corrupt-page doc
  (skipped surfaced); assert no page silently dropped.

## Group 5: Provider-agnostic LLM structuring + ResidencyGuard

- [ ] **5.1** Define `LLMStructuringClient` port + a mock/test adapter; expose
  `provider_id` and `endpoint`. No concrete vendor adapter (deferred).
  — size **S** · depends on: 1.1 · parallel: yes (with G3)
  · **Acceptance:** port is swappable; mock returns `ExtractedField[]`; selecting
  provider is config, not code (DX-FR-10).
  · **Tests:** mock adapter returns fields; swapping adapter needs no core change.
- [ ] **5.2** Implement `ResidencyGuard` wrapping the client: check
  `endpoint`/region against allow-list **before** any call; fail-closed when
  external inference disallowed.
  — size **M** · depends on: 5.1, 1.2 · parallel: no
  · **Acceptance:** non-allow-listed endpoint ⇒ `ResidencyError`, **no** outbound
  call; allow-listed ⇒ proceeds; `ALLOW_EXTERNAL_INFERENCE=false` blocks external.
  · **Tests:** denied endpoint raises + no call (spy); allowed endpoint proceeds;
  external-disabled blocks external but allows in-boundary; provider unavailable →
  `structuring_status="unavailable"` (graceful).
- [ ] **5.3** Confidence normalisation: clamp provider confidences to `[0,1]`;
  on unrecoverable value force `requires_verification=true` + warning.
  — size **S** · depends on: 5.1 · parallel: yes
  · **Acceptance:** out-of-range/NaN never propagates; always ends in `[0,1]`.
  · **Tests:** 1.3→1.0; -0.2→0.0; NaN→flagged; valid passes through.

## Group 6: Domain mapping + confidence gating

- [ ] **6.1** Implement `FieldMapper` driven by a `MappingProfile` (data-driven;
  no hard-coded form logic); drop unmapped/hallucinated keys with warning.
  — size **M** · depends on: 1.1, 5.3 · parallel: no
  · **Acceptance:** fields map to domain `key`/`target_type` per profile;
  unmapped keys dropped + warned; profile swap changes mapping without code change.
  · **Tests:** known profile maps fields; unmapped key dropped+warned; absent
  field not fabricated; two profiles yield different maps from same input.
- [ ] **6.2** Confidence threshold gating: set `requires_verification` for
  `confidence < threshold` (effective override) and rule-forced sensitive keys.
  — size **S** · depends on: 6.1 · parallel: no
  · **Acceptance:** below-threshold ⇒ flagged; at/above ⇒ not (unless rule-forced);
  override threshold respected.
  · **Tests:** below/at/above boundary; override applied; rule-forced key always
  flagged; no below-threshold field left unflagged.

## Group 7: MCP tool + prompt

- [ ] **7.1** Implement `document.extract` (risk tier `read`): validate → run
  pipeline → metadata audit → return proposal or async job handle. No connector
  write reachable.
  — size **M** · depends on: 2.x, 3.1, 4.1, 6.2 · parallel: no
  · **Acceptance:** returns `ExtractionProposal` for sync; job handle for async;
  self-describing schema (FR-17); never invokes a system-of-record write.
  · **Tests:** sync proposal shape; async handle + status; schema present;
  no-write assertion (connector spy never called); threshold-out-of-range rejected.
- [ ] **7.2** Author `extraction_schema` prompt (versioned), driven by active
  `MappingProfile`; instructs per-field `confidence ∈ [0,1]`, absent-if-unknown.
  — size **S** · depends on: 6.1 · parallel: yes
  · **Acceptance:** prompt renders from profile; output contract = `ExtractedField[]`;
  instructs no-guess + confidence range.
  · **Tests:** prompt renders for a profile; declares output shape + confidence rule.

## Group 8: Audit, observability, security hardening

- [ ] **8.1** Metadata audit record per run (no raw content/PII/values) via
  `platform-foundation` hash-chained log.
  — size **S** · depends on: 7.1 · parallel: no
  · **Acceptance:** one record/run with metadata only; chained; exportable.
  · **Tests:** record written; contains no content/values; chain verifies.
- [ ] **8.2** Structured spans/metrics + PII redaction in logs/traces.
  — size **S** · depends on: 7.1 · parallel: yes
  · **Acceptance:** spans per stage; metrics emitted; logs carry no raw text/PII.
  · **Tests:** redaction test (seed PII, assert absent in logs); spans present;
  residency-denied logged as security event.

## Group 9: PBT + docs

- [ ] **9.1** Implement the properties in `pbt-properties.md` with Hypothesis.
  — size **M** · depends on: 6.2, 7.1 · parallel: no
  · **Acceptance:** all listed properties pass; generators seeded with edge inputs.
  · **Tests:** the 5 properties (confidence range; low-confidence always flagged;
  never writes system of record; deterministic non-LLM idempotency; page coverage).
- [ ] **9.2** Feature docs (tool schema doc, prompt doc, `ASSUMPTION (confirm)`
  roll-up) + CI doc-freshness check per FR-18.
  — size **S** · depends on: 7.1, 7.2 · parallel: yes
  · **Acceptance:** tool/prompt self-described; assumptions listed; CI fails on
  schema/doc drift.
  · **Tests:** doc-check passes for current schema; fails on injected drift.

## Dependency graph (intra-spec + cross-spec)

```
platform-foundation#domain-model ─┐
platform-foundation#config-secrets┤
                                  ▼
                                 1.1 ──► 1.2
                                  │
            ┌──────────┬──────────┼──────────┐
            ▼          ▼          ▼          ▼
           2.1        2.2        5.1        (1.2)
            │          │          │
            ├────► 3.1  │          ▼
            │     │     │         5.2 ──► 5.3
            ▼     ▼     │          │
           4.1 ◄───────┘          ▼
            │                    6.1 ──► 6.2
            └─────────────┬───────┘       │
                          ▼               ▼
                         7.1 ◄────────── 7.2
                          │
                ┌─────────┼─────────┐
                ▼         ▼          ▼
               8.1       8.2        9.x
```

Cross-spec: all of G1 depends on `platform-foundation`. Consumers
(`client-intake`, `document-routing`) depend on `data-extraction#7.1`; they are
not prerequisites here.

## Definition of done (code + tests + docs)

A group is done only when: (1) **code** implements its acceptance criteria;
(2) **tests** — its 2–8 focused tests pass, plus relevant PBT properties from
G9; (3) **docs** — tool/prompt self-describe, `CONNECTOR.md`-equivalent not
applicable (no connector), feature doc + `ASSUMPTION (confirm)` roll-up updated,
CI doc-freshness green (FR-18). No raw content/PII in any log, trace, or audit
record is a release gate.
