# Property-Based Tests — Data Extraction

> Inherits `_shared/CONVENTIONS.md` (§3.7 PBT for invariants). Baseline:
> **Hypothesis** (PTD §3). These are invariants that must hold for *all*
> generated inputs; example-based cases live in `tasks.md` focused tests.
> Slug: `data-extraction`.

## Invariants under test (plain English)

1. **Confidence range.** Every extracted field's `confidence` is a real number in
   `[0,1]` — never <0, >1, NaN, or None. (DX-FR-4; spec §3 invariant.)
2. **Low-confidence always flagged.** Any field with `confidence < effective
   threshold` (or a rule-forced sensitive key) has `requires_verification = true`.
   No below-threshold field is ever silently accepted. (DX-FR-5.)
3. **Never writes a system of record.** Running `document.extract` over any input
   performs zero writes to any connector / system of record — it only proposes.
   (DX-FR-6, NFR-4.)
4. **Deterministic non-LLM idempotency.** With structuring disabled (text/OCR
   path only), identical input bytes + identical config produce identical text
   output and identical page coverage; `deterministic == True`. The LLM path is
   explicitly marked `deterministic == False`. (DX-FR-12, NFR-3.)
5. **Page coverage completeness.** The union of `text_pages`, `ocr_pages`, and
   `skipped_pages` equals exactly the full input page set `{1..total_pages}`,
   and the three sets are pairwise disjoint — no page is silently dropped between
   the text and OCR stages. (DX-FR-7.)

## Properties (Hypothesis-style pseudocode)

```python
from hypothesis import given, strategies as st, settings

# --- P1: confidence range -------------------------------------------------
@given(doc=documents())
def test_confidence_in_unit_interval(doc):
    proposal = extract(doc, structuring_client=MockLLM())
    for f in proposal.fields:
        assert isinstance(f.confidence, float)
        assert 0.0 <= f.confidence <= 1.0      # never <0, >1, NaN, None

# --- P2: low-confidence is always flagged --------------------------------
@given(doc=documents(), threshold=st.floats(0.0, 1.0))
def test_low_confidence_always_flagged(doc, threshold):
    proposal = extract(doc, threshold=threshold, structuring_client=MockLLM())
    for f in proposal.fields:
        if f.confidence < threshold or is_rule_forced(f.key):
            assert f.requires_verification is True
    # contrapositive: nothing below threshold is silently accepted
    assert not any(
        f.confidence < threshold and not f.requires_verification
        for f in proposal.fields
    )

# --- P3: never writes a system of record ---------------------------------
@given(doc=documents())
def test_extract_never_writes_system_of_record(doc):
    connectors = SpyConnectors()          # records any write call
    extract(doc, connectors=connectors, structuring_client=MockLLM())
    assert connectors.write_calls == []   # zero writes; proposal-only

# --- P4: deterministic non-LLM idempotency -------------------------------
@given(doc=documents(), cfg=configs())
def test_deterministic_text_path(doc, cfg):
    a = extract(doc, config=cfg, structuring=False)   # text/OCR only
    b = extract(doc, config=cfg, structuring=False)
    assert a.deterministic is True and b.deterministic is True
    assert text_of(a) == text_of(b)
    assert a.page_coverage == b.page_coverage
    # LLM path is explicitly non-deterministic
    c = extract(doc, config=cfg, structuring=True, structuring_client=MockLLM())
    assert c.deterministic is False

# --- P5: page coverage completeness & disjointness -----------------------
@given(doc=documents())
def test_page_coverage_is_total_and_disjoint(doc):
    cov = extract(doc, structuring_client=MockLLM()).page_coverage
    text, ocr, skip = set(cov.text_pages), set(cov.ocr_pages), set(cov.skipped_pages)
    full = set(range(1, cov.total_pages + 1))
    assert text | ocr | skip == full            # completeness: nothing dropped
    assert text.isdisjoint(ocr)                 # disjoint
    assert text.isdisjoint(skip) and ocr.isdisjoint(skip)
```

## Generators / input domains

- `documents()` — composite strategy emitting synthetic inputs across kinds:
  - native PDFs with a full text layer (every page has text),
  - scanned PDFs / images with no text layer (force OCR),
  - **mixed** PDFs (some pages text, some scanned),
  - PDFs with ≥1 corrupt/unprocessable page (force `skipped`),
  - email bodies (`total_pages == 0`, provenance `page=None`),
  - empty / zero-byte (expected to be rejected before stages),
  - varying page counts `st.integers(0, EXTRACTION_MAX_PAGES)`.
- `configs()` — thresholds in `[0,1]`, mapping-profile names, OCR langs,
  residency allow-list variants, `ALLOW_EXTERNAL_INFERENCE ∈ {True, False}`.
- `MockLLM()` — deterministic stub `LLMStructuringClient` returning a controlled
  `ExtractedField[]` (incl. occasionally out-of-range confidence to exercise
  clamping); `provider_id`/`endpoint` configurable to test residency.
- `SpyConnectors()` — records any attempted system-of-record write (must stay
  empty for P3).

## Known edge inputs to seed

- Confidence exactly `0.0` and exactly `1.0` (boundary inclusive).
- Confidence exactly at the threshold (at-threshold must **not** flag; below
  must flag).
- Provider-returned confidence `1.3`, `-0.2`, `NaN` → must be clamped/flagged,
  never propagated (P1).
- Single-page, zero-page (email body), and `EXTRACTION_MAX_PAGES` documents.
- All-text, all-OCR, mixed, and one-corrupt-page documents (P5).
- Provider unavailable → `structuring_status="unavailable"`, fields empty, still
  `deterministic` semantics intact (P3/P4 unaffected by structuring absence).
- Non-allow-listed endpoint → `ResidencyError`, no write, no leaked content
  (cross-check with `security-audit-prep.md`).
- Rule-forced sensitive key (e.g. A-number) at high confidence → still flagged
  (P2).
