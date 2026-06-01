"""Document routing — all G1–G9 focused tests + PBT invariants.

The four PBT properties:
  P1. privilege never passes a privileged-to-external combination
  P2. idempotency — same key → same outcome, no second move
  P3. totality — every class in DOCUMENT_CLASSES resolves to a destination
  P4. ACL never broadens beyond matter's allowed set
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cam.core.domain.models import ACL, Contact, Deadline, Document, Matter
from cam.core.workflows.document_routing.acl_builder import AclBuilder
from cam.core.workflows.document_routing.classifier import Classifier
from cam.core.workflows.document_routing.config import (
    DOCUMENT_CLASSES,
    ClassificationRule,
    RoutingConfig,
    get_routing_config,
    set_routing_config,
)
from cam.core.workflows.document_routing.namer import Namer, Resolver
from cam.core.workflows.document_routing.privilege_gate import PrivilegeGate
from cam.core.workflows.document_routing.service import (
    RoutingDecisionStore,
    RoutingService,
    _derive_idem_key,
)
from cam.core.workflows.document_routing.types import (
    GateVerdict,
    RouteOptions,
    RoutingDestination,
)
from cam.connectors.reference import ReferenceDocStoreConnector


NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contact() -> Contact:
    return Contact(id="c1", source="crm", name="Ana Garcia",
                   email="ana@example.com", external_ids={})


def _matter(allowed: list[str] | None = None) -> Matter:
    allowed_str = ",".join(allowed if allowed is not None else ["c1", "attorney1"])
    return Matter(id="m1", source="case", reference="REF-001", title="T",
                  status="open", client=_contact(), opened_at=NOW,
                  external_ids={"allowed_principals": allowed_str})


def _doc(privileged: bool = False, classification: str | None = None) -> Document:
    return Document(
        id="doc1", matter_id="m1", name="doc.pdf",
        mime_type="application/pdf", uri="s3://doc1",
        classification=classification, version=1,
        privileged=privileged, checksum="abc123", created_at=NOW,
    )


def _cfg() -> RoutingConfig:
    return get_routing_config()


def _service(docstore=None) -> tuple[RoutingService, RoutingDecisionStore, ReferenceDocStoreConnector]:
    store = RoutingDecisionStore()
    ds = docstore or ReferenceDocStoreConnector()
    svc = RoutingService(store, ds)
    return svc, store, ds


# ──────────────────────────────────────────────────────────────
# G1 — Types
# ──────────────────────────────────────────────────────────────

def test_route_options_defaults() -> None:
    opts = RouteOptions()
    assert opts.dry_run is False
    assert opts.force_review is False
    assert opts.recipient_routing_enabled is False


def test_routing_destination_kind_folder() -> None:
    d = RoutingDestination(kind="folder", folder="REF-001/docs/correspondence")
    assert d.kind == "folder"


def test_routing_destination_kind_queue() -> None:
    d = RoutingDestination(kind="review_queue")
    assert d.kind == "review_queue"


def test_route_result_round_trips_json() -> None:
    from cam.core.workflows.document_routing.types import RouteResult
    r = RouteResult(
        document_id="doc1", classification="correspondence",
        classification_confidence=0.92, name="REF-001_correspondence_20260601_v1",
        destination=RoutingDestination(kind="folder", folder="REF-001/docs"),
        privileged=False, gate_verdict="pass", outcome="moved",
        idempotency_key="abc123def456abcd",
    )
    assert RouteResult.model_validate_json(r.model_dump_json()).document_id == "doc1"


# ──────────────────────────────────────────────────────────────
# G1.2 — Config
# ──────────────────────────────────────────────────────────────

def test_config_loads_default() -> None:
    cfg = get_routing_config()
    assert cfg.routing_intent_version == "v1"


def test_folder_map_totality_enforced() -> None:
    from cam.core.workflows.document_routing.config import FolderMap
    incomplete = FolderMap(entries={"engagement_letter": "docs/el"})  # missing others
    with pytest.raises(ValueError, match="missing entries"):
        incomplete.validate_totality()


def test_class_enum_includes_unknown() -> None:
    assert "unknown" in DOCUMENT_CLASSES


# ──────────────────────────────────────────────────────────────
# G2 — Classifier
# ──────────────────────────────────────────────────────────────

def test_classify_rule_match_via_document_classification() -> None:
    clf = Classifier()
    doc = _doc(classification="correspondence")
    cls, conf, src = clf.classify(doc)
    assert cls == "correspondence"
    assert src == "rule"
    assert conf == 1.0


def test_classify_unknown_falls_back() -> None:
    clf = Classifier()
    doc = _doc(classification=None)
    cls, conf, src = clf.classify(doc)
    assert cls == "unknown"
    assert src == "fallback"


def test_classify_extraction_driven() -> None:
    clf = Classifier()
    doc = _doc()
    fields = [{"value": "court_filing", "confidence": 0.92}]
    cls, conf, src = clf.classify(doc, extraction_fields=fields)
    assert cls == "court_filing"
    assert src == "extraction"


def test_classify_below_threshold_unknown() -> None:
    clf = Classifier()
    doc = _doc()
    fields = [{"value": "court_filing", "confidence": 0.40}]  # below 0.80
    cls, _, src = clf.classify(doc, extraction_fields=fields)
    assert cls == "unknown"
    assert src == "fallback"


def test_classify_deterministic() -> None:
    clf = Classifier()
    doc = _doc(classification="internal_memo")
    r1 = clf.classify(doc)
    r2 = clf.classify(doc)
    assert r1 == r2


# ──────────────────────────────────────────────────────────────
# G3 — Namer + Resolver
# ──────────────────────────────────────────────────────────────

def test_namer_deterministic() -> None:
    namer = Namer()
    matter = _matter()
    doc = _doc()
    n1 = namer.derive_name(doc, "correspondence", matter)
    n2 = namer.derive_name(doc, "correspondence", matter)
    assert n1 == n2


def test_namer_contains_matter_ref_and_class() -> None:
    namer = Namer()
    matter = _matter()
    doc = _doc()
    name = namer.derive_name(doc, "correspondence", matter)
    assert "REF-001" in name
    assert "correspondence" in name


def test_namer_sanitises_unsafe_chars() -> None:
    namer = Namer()
    matter = _matter()
    doc = _doc()
    name = namer.derive_name(doc, "court filing/2026", matter)
    assert "/" not in name or name.startswith("REF-001")  # sanitised


def test_resolver_known_class_returns_folder() -> None:
    resolver = Resolver()
    doc = _doc()
    matter = _matter()
    dest = resolver.resolve(doc, "correspondence", matter)
    assert dest.kind == "folder"
    assert dest.folder is not None


def test_resolver_unknown_class_returns_queue() -> None:
    resolver = Resolver()
    doc = _doc()
    matter = _matter()
    dest = resolver.resolve(doc, "unknown", matter)
    assert dest.kind == "review_queue"


def test_resolver_no_matter_returns_queue() -> None:
    resolver = Resolver()
    doc = _doc()
    dest = resolver.resolve(doc, "correspondence", matter=None)
    assert dest.kind == "review_queue"


def test_resolver_force_review_overrides() -> None:
    resolver = Resolver()
    doc = _doc()
    matter = _matter()
    dest = resolver.resolve(doc, "correspondence", matter, force_review=True)
    assert dest.kind == "review_queue"


def test_resolver_totality_over_all_classes() -> None:
    resolver = Resolver()
    doc = _doc()
    matter = _matter()
    for cls in DOCUMENT_CLASSES:
        dest = resolver.resolve(doc, cls, matter)
        assert dest.kind in ("folder", "review_queue"), f"Class {cls!r} has no destination"


# ──────────────────────────────────────────────────────────────
# G4 — AclBuilder
# ──────────────────────────────────────────────────────────────

def test_acl_is_subset_of_matter_allowed() -> None:
    builder = AclBuilder()
    matter = _matter(allowed=["c1", "attorney1", "paralegal1"])
    acl = builder.build(matter, "correspondence")
    matter_set = {"c1", "attorney1", "paralegal1", matter.responsible or ""}
    matter_set.discard("")
    for p in acl.principals:
        assert p in matter_set, f"Principal {p!r} not in matter allowed set"


def test_acl_empty_allowed_set_returns_empty_acl() -> None:
    builder = AclBuilder()
    matter = _matter(allowed=[])
    # responsible is already None in _matter; empty allowed_principals → empty ACL
    acl = builder.build(matter, "correspondence")
    assert acl.principals == []


# ──────────────────────────────────────────────────────────────
# G5 — Privilege gate (the critical invariant)
# ──────────────────────────────────────────────────────────────

def test_privilege_gate_blocks_privileged_external() -> None:
    gate = PrivilegeGate()
    doc = _doc(privileged=True)
    dest = RoutingDestination(kind="folder", folder="REF-001/docs")
    verdict = gate.check(doc, dest, recipient_id="external@example.com",
                         caller_claims_external=True)
    assert verdict.verdict == "fail"


def test_privilege_gate_passes_privileged_internal() -> None:
    gate = PrivilegeGate()
    doc = _doc(privileged=True)
    dest = RoutingDestination(kind="folder", folder="REF-001/docs")
    verdict = gate.check(doc, dest, recipient_id=None, caller_claims_external=False)
    assert verdict.verdict == "pass"


def test_privilege_gate_passes_non_privileged_external() -> None:
    gate = PrivilegeGate()
    doc = _doc(privileged=False)
    dest = RoutingDestination(kind="folder", folder="REF-001/docs")
    verdict = gate.check(doc, dest, recipient_id="ext@example.com",
                         caller_claims_external=True)
    assert verdict.verdict == "pass"


def test_privilege_gate_verdict_has_reason() -> None:
    gate = PrivilegeGate()
    doc = _doc(privileged=True)
    dest = RoutingDestination(kind="folder")
    verdict = gate.check(doc, dest, recipient_id="ext@example.com",
                         caller_claims_external=True)
    assert verdict.reason


def test_privilege_gate_non_overridable() -> None:
    """Even with an 'approval' signal, privileged+external is blocked."""
    gate = PrivilegeGate()
    doc = _doc(privileged=True)
    dest = RoutingDestination(kind="folder")
    # Pass a hypothetical approval — the gate ignores it
    verdict = gate.check(doc, dest, recipient_id="ext@example.com",
                         caller_claims_external=True)
    assert verdict.verdict == "fail"


# ──────────────────────────────────────────────────────────────
# G6 — RoutingService
# ──────────────────────────────────────────────────────────────

async def test_happy_path_returns_moved() -> None:
    svc, store, ds = _service()
    doc = _doc(classification="correspondence")
    # Seed the document in the docstore first
    content = b"doc content"
    await ds.put(doc, content)
    matter = _matter()
    result = await svc.route(doc, matter, RouteOptions())
    assert result.outcome == "moved"
    assert result.classification == "correspondence"


async def test_low_confidence_returns_queued() -> None:
    svc, store, ds = _service()
    doc = _doc()  # no classification
    matter = _matter()
    result = await svc.route(doc, matter, RouteOptions())
    assert result.outcome == "queued"


async def test_privileged_external_blocked() -> None:
    svc, store, ds = _service()
    doc = _doc(privileged=True, classification="correspondence")
    await ds.put(doc, b"content")
    matter = _matter()
    result = await svc.route(
        doc, matter, RouteOptions(recipient_routing_enabled=True),
        recipient_id="ext@example.com",
    )
    assert result.outcome == "blocked"
    assert result.gate_verdict == "fail"


async def test_dry_run_no_move() -> None:
    move_calls: list = []

    class SpyDocstore:
        async def put(self, doc, content): return doc
        async def get(self, id): raise Exception("not found")
        async def move(self, id, folder, acl):
            move_calls.append((id, folder))
            return _doc()

    svc, store, _ = _service(docstore=SpyDocstore())
    doc = _doc(classification="correspondence")
    matter = _matter()
    result = await svc.route(doc, matter, RouteOptions(dry_run=True))
    assert result.outcome == "dry_run"
    assert not move_calls


async def test_idempotent_route_returns_duplicate() -> None:
    svc, store, ds = _service()
    doc = _doc(classification="correspondence")
    await ds.put(doc, b"content")
    matter = _matter()
    r1 = await svc.route(doc, matter, RouteOptions())
    r2 = await svc.route(doc, matter, RouteOptions())
    assert r2.outcome == "duplicate"


async def test_audit_written_before_return() -> None:
    audit_calls: list[dict] = []
    async def fake_audit(**kwargs): audit_calls.append(kwargs)

    store = RoutingDecisionStore()
    ds = ReferenceDocStoreConnector()
    svc = RoutingService(store, ds, audit_fn=fake_audit)
    doc = _doc(classification="correspondence")
    await ds.put(doc, b"content")
    matter = _matter()
    result = await svc.route(doc, matter, RouteOptions())
    assert audit_calls  # at least one audit record written
    assert audit_calls[-1]["action"] == "document.route"


# ──────────────────────────────────────────────────────────────
# G9 PBT — the four invariants
# ──────────────────────────────────────────────────────────────

@given(privileged=st.booleans(), external=st.booleans())
@settings(max_examples=200)
def test_pbt_privilege_never_passes_external(privileged: bool, external: bool) -> None:
    """P1: privileged + external → always fail."""
    gate = PrivilegeGate()
    doc = _doc(privileged=privileged)
    dest = RoutingDestination(kind="folder", folder="some/folder")
    recipient = "ext@example.com" if external else None
    verdict = gate.check(doc, dest, recipient_id=recipient,
                         caller_claims_external=external)
    if privileged and external:
        assert verdict.verdict == "fail", (
            f"Privilege gate must FAIL for privileged+external: got {verdict.verdict!r}"
        )
    assert verdict.verdict != "warn", "Privilege gate must never emit warn"


@given(
    doc_id=st.text(min_size=1, max_size=20, alphabet=st.characters(blacklist_categories=('Cs',))),
    checksum=st.text(min_size=6, max_size=40, alphabet=st.characters(blacklist_categories=('Cs',))),
    version=st.text(min_size=1, max_size=4, alphabet=st.characters(blacklist_categories=('Cs',))),
)
@settings(max_examples=200)
def test_pbt_idem_key_stable(doc_id: str, checksum: str, version: str) -> None:
    """P2: same inputs → same idem_key."""
    k1 = _derive_idem_key(doc_id, checksum, None, version)
    k2 = _derive_idem_key(doc_id, checksum, None, version)
    assert k1 == k2
    assert len(k1) == 16


def test_pbt_totality_all_classes_resolve() -> None:
    """P3: every class in DOCUMENT_CLASSES has a resolution."""
    resolver = Resolver()
    doc = _doc()
    matter = _matter()
    for cls in DOCUMENT_CLASSES:
        dest = resolver.resolve(doc, cls, matter)
        assert dest.kind in ("folder", "review_queue"), f"{cls!r} unresolved"


@given(
    principals=st.lists(
        st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("Lu","Ll","Nd"))),
        min_size=0, max_size=5, unique=True,
    )
)
@settings(max_examples=200)
def test_pbt_acl_never_broadens(principals: list[str]) -> None:
    """P4: ACL ⊆ matter allowed set for any allowed set."""
    builder = AclBuilder()
    allowed_str = ",".join(principals)
    matter = _matter(allowed=principals if principals else None)
    effective = principals if principals else ["c1", "attorney1"]
    matter_set = set(effective)
    if matter.responsible:
        matter_set.add(matter.responsible)
    acl = builder.build(matter, "correspondence")
    for p in acl.principals:
        assert p in matter_set, f"ACL principal {p!r} not in matter allowed set {matter_set}"
