# Property-Based Tests — Document Routing

> Feature slug: `document-routing` · Traces: FR-11, NFR-1 · Stack: Python +
> **Hypothesis** (CONVENTIONS §4). These properties assert invariants that must
> hold for **all** inputs; focused/example tests live in `tasks.md`. Pseudocode
> is illustrative (real tests use the actual services with `data-extraction` /
> `qc-verification` / `DocStoreConnector` test doubles).

## Invariants under test (plain English)

1. **Privilege never routes external (HARD invariant, NFR-1).** For any document
   marked privileged (or of unknown privilege), routing to any external
   recipient/destination is **blocked** — the move/share is never performed.
2. **Routing idempotency.** Routing the same document with the same intent files
   it **exactly once**; any repeat returns the prior result without a second move.
3. **Classification → destination totality.** Every class in the configured
   enumeration maps to a concrete folder **or** the explicit `review_queue`; there
   is no input for which routing has no destination.
4. **ACL never broadens.** An applied ACL is always a **subset** of the resolved
   matter's allowed-principal set — routing never grants access beyond it.

## Properties (Hypothesis-style pseudocode)

```python
from hypothesis import given, assume, settings, HealthCheck

# ── P1: privileged/unknown doc is NEVER routed to an external recipient ────────
@settings(suppress_health_check=[HealthCheck.too_slow])
@given(doc=documents(), dest=destinations(), recipient=recipients())
def test_privileged_never_external(doc, dest, recipient):
    target_is_external = is_external(dest, recipient, matter_allowed_set(doc.matter_id))
    assume(doc.privileged is True or doc.privileged is None)  # None = unknown → default-deny
    assume(target_is_external)
    store = RecordingDocStore()                 # records any move/share
    result = RoutingService(store=store).route(
        RouteRequest(document_id=doc.id, recipient_id=recipient and recipient.id)
    )
    assert result.outcome == "blocked"
    assert result.gate_verdict == "fail"
    assert store.moves == [] and store.shares == []   # nothing left the boundary

# ── P2: routing is idempotent — filed exactly once ────────────────────────────
@given(req=route_requests())
def test_routing_idempotent(req):
    store = CountingDocStore()
    svc = RoutingService(store=store)
    first = svc.route(req)
    second = svc.route(req)               # identical document_id + checksum + intent
    assert second.outcome == "duplicate"
    assert second.destination == first.destination
    # at most one real move regardless of repeats
    assert store.move_count <= 1
    if first.outcome == "moved":
        assert store.move_count == 1

# ── P3: classification → destination mapping is total ─────────────────────────
@given(doc=documents(), cls=sampled_from(CONFIGURED_CLASS_ENUM))
def test_classification_destination_totality(doc, cls):
    dest = Resolver(folder_map=loaded_folder_map()).resolve(doc, cls)
    assert dest.kind in {"folder", "review_queue"}
    if dest.kind == "folder":
        assert dest.folder is not None and dest.matter_id is not None
    # never an undefined / empty destination for any class in the enum
    assert dest is not None

# ── P4: applied ACL never broadens beyond the matter's allowed set ────────────
@given(matter=matters(), cls=sampled_from(CONFIGURED_CLASS_ENUM))
def test_acl_is_subset_of_allowed(matter, cls):
    allowed = set(matter_allowed_set(matter.id))
    acl = AclBuilder().build(matter, cls)
    assert set(acl.principals) <= allowed            # subset, never a superset
    # least-privilege: class policy can only narrow, never widen
    assert set(acl.principals) <= set(class_policy_principals(cls)) | allowed
```

## Generators / input domains

- `documents()` — `Document` with varied `privileged ∈ {True, False, None}`,
  random `matter_id` (resolvable + unresolvable), varied `classification`,
  `checksum`, `version`.
- `destinations()` — `RoutingDestination` spanning internal matter folders,
  `review_queue`, and out-of-matter folders (to exercise external detection).
- `recipients()` — `None`, an in-matter principal, an out-of-matter/firm-external
  `Contact`, and the client themselves (privilege-holder edge case).
- `route_requests()` — `RouteRequest` with/without recipient, `force_review`,
  `dry_run`, and repeated identical requests (for P2).
- `matters()` — `Matter` with allowed-principal sets of size 0..N (incl. empty).
- `CONFIGURED_CLASS_ENUM` — the full loaded class enumeration incl. `unknown`
  (drives totality P3).
- `is_external(...)` — the spec's external-vs-internal predicate (per
  `requirements.md` §8 ASSUMPTION); generators feed both sides of the boundary.

## Known edge inputs to seed

- Privileged doc + recipient = **the client themselves** (privilege holder) —
  confirm against the §8 ASSUMPTION; default-deny until confirmed.
- `privileged = None` (unknown) + external target → must block (default-deny).
- Class **not** present in the folder-map at runtime (should be impossible: load-time
  config check) → P3 still yields `review_queue` if it ever occurs.
- Empty matter allowed set → ACL must be empty (no default-open) — P4.
- Two concurrent identical `RouteRequest`s → exactly one move (P2 under race;
  unique idempotency-key constraint + lock).
- Content-changed document (new `checksum`) → new idempotency key → a new,
  separate decision (not a duplicate).
- `dry_run=True` on a routable doc → no move recorded, but decision audited.
- Out-of-matter folder as destination for a privileged doc → treated as external →
  blocked.
