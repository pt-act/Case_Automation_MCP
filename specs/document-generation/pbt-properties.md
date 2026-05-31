# Property-Based Tests — Document Generation

> Feature slug: `document-generation` · Phase 4. Invariants that must hold for
> *all* inputs (Hypothesis, the baseline PBT tool — CONVENTIONS §4). Focused
> example tests live in `tasks.md`; these cover the PBT FOCUS items: no silent
> blank, monotonic non-overwriting versioning, checksum integrity, render
> determinism, all-vars-resolved ⇒ QC completeness pass, idempotency.

---

## Invariants under test (plain English)

1. **No silent blank.** For any template and any data context, every *required*
   template variable that is unresolved (missing/empty/type-mismatch/enum) appears
   in `result.gaps` and is rendered as a visible gap placeholder — never as an
   empty string in the output (FR-9).
2. **Monotonic, non-overwriting versioning.** For any sequence of distinct
   generations against the same `(matter_id, template_name, doc_key)`, assigned
   versions are strictly increasing by 1, and no prior version's bytes are ever
   mutated or replaced (DG-FR-8, NFR-3).
3. **Checksum integrity.** For any generation that stores a document, the bytes
   read back from the `DocStoreConnector` equal the rendered bytes, and the stored
   `checksum` equals the checksum of those bytes.
4. **Render determinism.** For any template and any fixed data context, rendering
   twice produces byte-identical output (hence identical checksum), given a
   deterministic template (non-deterministic templates are rejected at load).
5. **All-resolved ⇒ completeness passes.** For any template, if the resolved
   context satisfies every required variable (no gaps), the QC completeness check
   returns `pass` (FR-13).
6. **Idempotency.** For any generation, repeating it with the same idempotency key
   (same template version + same canonical context) returns the existing
   `Document` and creates no new version (NFR-3).

---

## Properties (Hypothesis-style pseudocode)

```python
from hypothesis import given, strategies as st, assume, settings

# ---- Property 1: no silent blank ----------------------------------------
@given(template=templates(), context=data_contexts())
def test_no_silent_blank(template, context):
    result = generate(template, context)
    resolved = resolve_context(template, context)
    for var in template.variables:
        if var.required and not is_satisfied(resolved, var):
            # surfaced as a gap...
            assert any(g.variable == var.name for g in result.gaps)
            # ...and visible in the artefact, never an empty slot
            assert gap_placeholder(var) in rendered_text(result)
            assert empty_slot_for(var) not in rendered_text(result)
    if result.gaps:
        assert result.status == "incomplete"


# ---- Property 2: monotonic, non-overwriting versioning ------------------
@given(gens=st.lists(distinct_generations(), min_size=1, max_size=8))
def test_versions_monotonic_and_immutable(gens):
    matter_id, template, doc_key = same_target()
    seen_versions, frozen_bytes = [], {}
    for ctx in gens:
        r = generate(template, ctx, matter_id=matter_id, doc_key=doc_key)
        if r.status == "failed":
            continue
        v = r.document.version
        assert v == (seen_versions[-1] + 1 if seen_versions else 1)
        seen_versions.append(v)
        # prior versions never mutated
        for pv, b in frozen_bytes.items():
            assert docstore_get(matter_id, template.name, doc_key, pv) == b
        frozen_bytes[v] = docstore_get(matter_id, template.name, doc_key, v)
    assert seen_versions == sorted(set(seen_versions))


# ---- Property 3: checksum integrity -------------------------------------
@given(template=templates(), context=data_contexts())
def test_checksum_matches_stored_bytes(template, context):
    r = generate(template, context)
    assume(r.document is not None)
    stored = docstore_get_bytes(r.document.uri)
    assert stored == rendered_bytes(r)
    assert r.document.checksum == checksum(stored)


# ---- Property 4: render determinism -------------------------------------
@given(template=deterministic_templates(), context=data_contexts())
@settings(max_examples=200)
def test_render_is_deterministic(template, context):
    a = render_docx(template, context)
    b = render_docx(template, context)
    assert a == b
    assert checksum(a) == checksum(b)


# ---- Property 5: all-resolved => completeness passes --------------------
@given(template=templates(), context=complete_contexts())  # fills every required var
def test_all_resolved_implies_qc_completeness_pass(template, context):
    r = generate(template, context)
    assert r.gaps == []
    completeness = qc_check(r, "completeness")
    assert completeness == "pass"
    assert r.status in ("ready", "blocked")  # blocked only if a *non*-completeness check fails
    if r.qc_verdict == "pass":
        assert r.status == "ready"


# ---- Property 6: idempotency --------------------------------------------
@given(template=templates(), context=data_contexts())
def test_idempotent_regeneration(template, context):
    first = generate(template, context)
    assume(first.document is not None)
    second = generate(template, context)  # same template version + context
    assert second.document.version == first.document.version
    assert second.document.checksum == first.document.checksum
    assert version_count(first.document) == 1  # no extra version created
```

> Property 2 note: `distinct_generations()` must yield contexts that differ in
> content (so each is a genuinely new version) while sharing the same target
> `(matter_id, template_name, doc_key)`. Idempotent repeats are covered by
> Property 6, not Property 2.

---

## Generators / input domains

- **`templates()`** — `TemplateSpec` with 1–20 variables; mix of
  required/optional; types ∈ {string, date, number, bool, address, enum};
  enum variables always carry `enum_values`. Template bytes contain exactly the
  declared Jinja2 variables.
- **`deterministic_templates()`** — `templates()` filtered to reject any template
  referencing time/random/side-effecting helpers (these are rejected at load,
  task 1.4).
- **`data_contexts()`** — maps from a subset of declared variable names to values
  of correct *or* incorrect type/empty/enum-violating, so gaps arise naturally.
  Values drawn from immigration-plausible domains (names, dates, A-number-shaped
  strings) but **synthetic** — never real PII.
- **`complete_contexts()`** — `data_contexts()` constrained so every required
  variable is satisfied with a correctly-typed, non-empty, enum-valid value.
- **`distinct_generations()`** — lists of contexts guaranteed pairwise distinct in
  resolved content, all targeting one `(matter_id, template_name, doc_key)`.
- **Targets** — `same_target()` fixes a single `(matter_id, template_name,
  doc_key)` triple per versioning property run.

## Known edge inputs to seed

- Template with **zero required** variables (always `ready`, no gaps).
- Template where **every** variable is required and context is empty (all gaps;
  `incomplete`; full placeholder coverage).
- Variable present but **empty string** / whitespace-only (must gap as `empty`).
- **Type mismatch**: string supplied where `date`/`number` required.
- **Enum violation**: value outside `enum_values`.
- **Unicode / RTL / very long** values (determinism + no truncation surprises).
- Two generations with **identical content** (idempotency hit; no new version).
- Two generations with **different content**, same target (versions 1 then 2).
- Concurrent generations on the same target (distinct versions; atomic ledger).
- DocStore **transient error then success** on retry (same idempotency key; no
  duplicate version).
- Idempotency-key **collision with different content** (must refuse, not
  overwrite).
- Non-deterministic template referencing `now()` (rejected at load).
