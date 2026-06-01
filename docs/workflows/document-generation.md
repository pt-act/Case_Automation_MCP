# Document Generation — Technical Reference

## Tools
- `document.generate` — risk tier write (confirm); reversible draft stored, nothing sent/filed
- `form.prefill` — risk tier read (fields_only) or write/confirm (document)

## Pipeline
1. Load template (TemplateStore)
2. Resolve context (Matter/Contact + data_context overrides)
3. Detect gaps (required vars missing/empty/type_mismatch/enum_violation)
4. Idempotency check (idem_key → existing Document → return unchanged)
5. Render DOCX (docxtpl + Jinja2); gap vars → [[MISSING: label]] placeholder
6. PDF convert (configurable engine; DOCX-only if unavailable)
7. Checksum rendered primary artefact
8. QC (completeness + consistency)
9. Store via DocStoreConnector.put (monotonic version per matter+template+doc_key)
10. Audit record (before "complete"; digest not raw PII)
11. Return GenerationResult

## Status derivation
- gaps present → `incomplete` (even if QC passes)
- QC `fail` → `blocked`
- QC `skipped` (unavailable) → `incomplete` (cannot certify)
- QC `pass`/`warn` + no gaps → `ready`
- Render/store error → `failed`

## Idempotency
- idem_key = explicit key OR sha256(template_name + version + canonical(context))[:24]
- Same key → return existing Document, no new version
- Same key + differing content → VersionCollisionError (refused)

## Versioning
- Monotonic per (matter_id, template_name, doc_key)
- Old versions immutable
- Document.checksum = sha256(rendered primary bytes)

## Gap placeholders
- Unresolved required vars → `[[MISSING: label]]` token in output
- Never an empty string (FR-9)
- Stored even when incomplete/blocked (human has concrete artefact to fix)

## ASSUMPTION (confirm)
- Default PDF engine (LibreOffice vs WeasyPrint) and primary format (DOCX vs HTML)
- Behaviour when PDF mandatory and conversion fails
- Behaviour when QC unavailable (block vs warn) — current: incomplete
- Priority form IDs for form.prefill (I-130, I-485, N-400, G-28)
- doc_key definition for version ledger
- Default privilege classification (currently: true)
