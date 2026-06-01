# QC Verification — Technical Reference

## Overview
The QC Verification service is a **pluggable check registry** that runs a set of
composable checks against a `VerificationPacket` and returns a `QCReport`.

**Key invariant:** any single `fail` verdict → aggregate = `block`.

## Verdict model
| Verdict | Aggregate impact |
|---------|-----------------|
| `pass` | contributes to `pass` or `pass_with_warnings` |
| `warn` | → `pass_with_warnings` (unless a fail exists) |
| `fail` | → `block` immediately |
| `skipped` | ignored in aggregation; listed in `checks_skipped` |

## The seven built-in checks
| Check ID | Verifies | Can emit `warn`? |
|---|---|---|
| `completeness` | Required template vars + matter fields | yes |
| `consistency` | Name/ref consistency across artefacts | yes |
| `attachment_integrity` | Attachments present + checksum | yes |
| `recipient_integrity` | Recipients ∈ matter participants | yes |
| `privilege` | No privileged doc to external recipient | **no — fail or pass only** |
| `deadline_sanity` | Deadlines plausible, not past-due | yes |
| `extraction_confidence` | Field confidence thresholds | yes |

## Adding a new check

1. Create a class with `id`, `version`, `applies_to`, `severity_policy`,
   `required_inputs`, `describe()`, and `run()`.
2. Call `register_check(MyCheck())` at startup (before any `tool_qc_verify` calls).
3. The registry runs it automatically for matching `PacketKind`s.
4. No edits to registry core needed (FR-16).

## Integration boundary
Callers build a `VerificationPacket` and call `tool_qc_verify`.
Treat `aggregate == "block"` as a hard stop.

## Security notes
- `privilege` never emits `warn` — it is fail-closed.
- A check error → `fail` (never silently passes).
- `packet.now` is always injected — no `datetime.now()` inside checks.
