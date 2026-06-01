"""Document generation — all G1–G7 focused tests + PBT invariants."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cam.core.domain.models import Contact, Matter
from cam.core.services.qc.checks import register_all
from cam.core.services.qc.registry import clear_registry as qc_clear
from cam.core.workflows.document_gen.context import build_context, detect_gaps
from cam.core.workflows.document_gen.renderer import GAP_TOKEN, checksum, render_docx
from cam.core.workflows.document_gen.template_store import (
    TemplateNotFound,
    TemplateStore,
    TemplateValidationError,
    make_simple_template,
)
from cam.core.workflows.document_gen.tools import (
    tool_document_generate,
    tool_form_prefill,
)
from cam.core.workflows.document_gen.types import Gap, GenerationResult, TemplateSpec, TemplateVariable
from cam.core.workflows.document_gen.version_store import (
    InMemoryVersionLedger,
    VersionCollisionError,
    derive_idem_key,
    store_document,
)
from cam.connectors.reference import ReferenceDocStoreConnector


NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_qc():
    qc_clear()
    register_all()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contact(with_email: bool = True) -> Contact:
    return Contact(
        id="c1", source="crm", name="Ana Garcia",
        email="ana@example.com" if with_email else None,
        external_ids={},
    )


def _matter(client: Contact | None = None) -> Matter:
    c = client or _contact()
    return Matter(id="m1", source="case", reference="REF-001", title="T",
                  status="open", client=c, opened_at=NOW, external_ids={})


def _spec(*var_names: str, required: bool = True) -> TemplateSpec:
    return TemplateSpec(
        name="test_tpl",
        version=1,
        format="docx",
        variables=[
            TemplateVariable(name=n, label=n.replace(".", " "), required=required, type="string")
            for n in var_names
        ],
    )


def _store(*var_names: str, required: bool = True) -> TemplateStore:
    spec = _spec(*var_names, required=required)
    store = TemplateStore()
    content = make_simple_template(spec)
    store.register(spec, content)
    return store


def _components():
    ledger = InMemoryVersionLedger()
    docstore = ReferenceDocStoreConnector()
    return ledger, docstore


# ──────────────────────────────────────────────────────────────
# G1 — Template model + store
# ──────────────────────────────────────────────────────────────

def test_template_spec_round_trip() -> None:
    spec = _spec("client.full_name")
    dumped = spec.model_dump_json()
    reloaded = TemplateSpec.model_validate_json(dumped)
    assert reloaded.name == spec.name


def test_required_default_is_true() -> None:
    var = TemplateVariable(name="x", label="X", type="string")
    assert var.required is True


def test_enum_type_with_values() -> None:
    var = TemplateVariable(name="status", label="Status", type="enum", enum_values=["A", "B"])
    assert var.enum_values == ["A", "B"]


def test_template_store_known_name() -> None:
    store = _store("client.full_name")
    spec, content = store.get("test_tpl")
    assert spec.name == "test_tpl"
    assert content


def test_template_store_unknown_raises() -> None:
    store = TemplateStore()
    with pytest.raises(TemplateNotFound):
        store.get("nonexistent")


def test_nondeterministic_template_rejected() -> None:
    spec = _spec("client.full_name")
    bad_content = b"{{ client.full_name }} generated at {{ now() }}"
    store = TemplateStore()
    with pytest.raises(TemplateValidationError, match="non-deterministic"):
        store.register(spec, bad_content)


def test_undeclared_variable_rejected() -> None:
    spec = _spec("client.full_name")  # declares only client.full_name
    bad_content = b"{{ client.full_name }} {{ undeclared_var }}"
    store = TemplateStore()
    with pytest.raises(TemplateValidationError, match="undeclared"):
        store.register(spec, bad_content)


def test_template_resource_lists_all_vars() -> None:
    store = _store("client.full_name", "matter.reference")
    resource = store.resource_template("test_tpl")
    names = {v["name"] for v in resource["variables"]}
    assert "client.full_name" in names
    assert "matter.reference" in names


# ──────────────────────────────────────────────────────────────
# G2 — Context resolution + gap detection
# ──────────────────────────────────────────────────────────────

def test_domain_only_context() -> None:
    spec = _spec("client.full_name", "matter.reference")
    matter = _matter()
    ctx = build_context(spec, matter)
    assert ctx["client.full_name"] == "Ana Garcia"
    assert ctx["matter.reference"] == "REF-001"


def test_override_precedence() -> None:
    spec = _spec("client.full_name")
    matter = _matter()
    ctx = build_context(spec, matter, data_context={"client.full_name": "Override Name"})
    assert ctx["client.full_name"] == "Override Name"


def test_unknown_override_key_ignored() -> None:
    spec = _spec("client.full_name")
    matter = _matter()
    ctx = build_context(spec, matter, data_context={"unknown_key": "value"})
    assert "unknown_key" not in ctx


def test_missing_required_produces_gap() -> None:
    spec = _spec("client.email")
    matter = _matter(client=_contact(with_email=False))
    ctx = build_context(spec, matter)
    gaps = detect_gaps(spec, ctx)
    assert any(g.variable == "client.email" and g.reason == "missing" for g in gaps)


def test_empty_required_produces_gap() -> None:
    spec = _spec("client.full_name")
    matter = _matter()
    ctx = {"client.full_name": "   "}
    gaps = detect_gaps(spec, ctx)
    assert gaps[0].reason == "empty"


def test_enum_violation_produces_gap() -> None:
    spec = TemplateSpec(name="t", version=1, format="docx", variables=[
        TemplateVariable(name="status", label="Status", required=True,
                         type="enum", enum_values=["A", "B"])
    ])
    gaps = detect_gaps(spec, {"status": "C"})
    assert gaps[0].reason == "enum_violation"


def test_optional_missing_no_gap() -> None:
    spec = _spec("opt_field", required=False)
    gaps = detect_gaps(spec, {"opt_field": None})
    assert not gaps


# ──────────────────────────────────────────────────────────────
# G3 — Renderer
# ──────────────────────────────────────────────────────────────

def test_render_fills_all_vars() -> None:
    template = b"Name: {{ client.full_name }} Ref: {{ matter.reference }}"
    ctx = {"client.full_name": "Ana Garcia", "matter.reference": "REF-001"}
    rendered = render_docx(template, ctx, [])
    assert b"Ana Garcia" in rendered
    assert b"REF-001" in rendered


def test_render_gap_produces_placeholder() -> None:
    template = b"Name: {{ client.full_name }}"
    ctx = {"client.full_name": None}
    gap = Gap(variable="client.full_name", label="Client Full Name", reason="missing")
    rendered = render_docx(template, ctx, [gap])
    assert b"[[MISSING: Client Full Name]]" in rendered


def test_render_identical_inputs_identical_bytes() -> None:
    template = b"{{ client.full_name }}"
    ctx = {"client.full_name": "Ana Garcia"}
    r1 = render_docx(template, ctx, [])
    r2 = render_docx(template, ctx, [])
    assert r1 == r2


def test_checksum_stable() -> None:
    data = b"hello world"
    assert checksum(data) == checksum(data)


def test_checksum_differs_on_different_bytes() -> None:
    assert checksum(b"hello") != checksum(b"world")


# ──────────────────────────────────────────────────────────────
# G4 — Versioning + idempotency
# ──────────────────────────────────────────────────────────────

def test_idem_key_stable() -> None:
    k1 = derive_idem_key("tpl", 1, {"a": "1"})
    k2 = derive_idem_key("tpl", 1, {"a": "1"})
    assert k1 == k2


def test_idem_key_differs_on_different_context() -> None:
    k1 = derive_idem_key("tpl", 1, {"a": "1"})
    k2 = derive_idem_key("tpl", 1, {"a": "2"})
    assert k1 != k2


def test_version_monotonic() -> None:
    ledger = InMemoryVersionLedger()
    v1 = ledger.next_version("m1", "tpl", "key")
    v2 = ledger.next_version("m1", "tpl", "key")
    assert v2 == v1 + 1


async def test_store_round_trip() -> None:
    ledger = InMemoryVersionLedger()
    docstore = ReferenceDocStoreConnector()
    content = b"document content here"
    csum = checksum(content)

    from cam.core.domain.models import Document
    from datetime import datetime, timezone

    stored = await store_document(
        matter_id="m1", template_name="tpl", template_version=1,
        doc_key="key", rendered_bytes=content, rendered_checksum=csum,
        privileged=True, classification="test", idem_key="idem-001",
        ledger=ledger, docstore=docstore,
    )
    assert stored.version == 1
    assert stored.checksum == csum


async def test_idempotent_store_returns_existing() -> None:
    ledger = InMemoryVersionLedger()
    docstore = ReferenceDocStoreConnector()
    content = b"same content"
    csum = checksum(content)

    s1 = await store_document(
        matter_id="m1", template_name="tpl", template_version=1,
        doc_key="key", rendered_bytes=content, rendered_checksum=csum,
        privileged=True, classification="test", idem_key="idem-001",
        ledger=ledger, docstore=docstore,
    )
    s2 = await store_document(
        matter_id="m1", template_name="tpl", template_version=1,
        doc_key="key", rendered_bytes=content, rendered_checksum=csum,
        privileged=True, classification="test", idem_key="idem-001",
        ledger=ledger, docstore=docstore,
    )
    assert s1.id == s2.id  # same document returned


# ──────────────────────────────────────────────────────────────
# G6 — document.generate tool
# ──────────────────────────────────────────────────────────────

async def test_happy_path_returns_ready() -> None:
    store = _store("client.full_name", "matter.reference")
    ledger, docstore = _components()
    matter = _matter()
    result = await tool_document_generate(
        matter=matter, template_name="test_tpl",
        template_store=store, ledger=ledger, docstore=docstore,
    )
    assert result.status == "ready"
    assert result.document is not None
    assert not result.gaps


async def test_missing_required_returns_incomplete() -> None:
    store = _store("client.email")  # email required but client has none
    ledger, docstore = _components()
    matter = _matter(client=_contact(with_email=False))
    result = await tool_document_generate(
        matter=matter, template_name="test_tpl",
        template_store=store, ledger=ledger, docstore=docstore,
    )
    assert result.status == "incomplete"
    assert result.gaps


async def test_unknown_template_returns_failed() -> None:
    store = TemplateStore()
    ledger, docstore = _components()
    matter = _matter()
    result = await tool_document_generate(
        matter=matter, template_name="nonexistent",
        template_store=store, ledger=ledger, docstore=docstore,
    )
    assert result.status == "failed"


async def test_idempotent_generate_no_new_version() -> None:
    store = _store("client.full_name")
    ledger, docstore = _components()
    matter = _matter()
    r1 = await tool_document_generate(
        matter=matter, template_name="test_tpl",
        template_store=store, ledger=ledger, docstore=docstore,
        idem_key_override="fixed-key",
    )
    r2 = await tool_document_generate(
        matter=matter, template_name="test_tpl",
        template_store=store, ledger=ledger, docstore=docstore,
        idem_key_override="fixed-key",
    )
    assert r1.document.id == r2.document.id
    assert r1.document.version == r2.document.version


async def test_audit_record_written() -> None:
    store = _store("client.full_name")
    ledger, docstore = _components()
    matter = _matter()
    records: list[dict] = []
    async def fake_audit(**kwargs): records.append(kwargs)
    await tool_document_generate(
        matter=matter, template_name="test_tpl",
        template_store=store, ledger=ledger, docstore=docstore,
        audit_fn=fake_audit,
    )
    # Multiple audit records may be written (QC + docgen); find the docgen one
    docgen_records = [r for r in records if r.get("action") == "document.generate"]
    assert docgen_records, "Expected at least one document.generate audit record"
    rec = docgen_records[0]
    assert "context_digest" in rec["inputs"]
    # Verify no raw PII values in the docgen record inputs/outputs
    assert "Ana Garcia" not in str(rec["inputs"]) and "Ana Garcia" not in str(rec["outputs"])


# ──────────────────────────────────────────────────────────────
# G6 — form.prefill
# ──────────────────────────────────────────────────────────────

async def test_prefill_fields_only_no_fabrication() -> None:
    # Register a form template
    spec = TemplateSpec(name="i130_petition", version=1, format="docx", variables=[
        TemplateVariable(name="client.full_name", label="Name", required=True, type="string"),
        TemplateVariable(name="client.email", label="Email", required=False, type="string"),
    ])
    store = TemplateStore()
    store.register(spec, make_simple_template(spec))
    ledger, docstore = _components()
    matter = _matter(client=_contact(with_email=False))

    result = await tool_form_prefill(
        matter=matter, form_id="I-130",
        template_store=store, ledger=ledger, docstore=docstore,
        emit="fields_only",
    )
    assert "resolved_fields" in result
    assert result["form_id"] == "I-130"
    # No fabricated value — client.full_name should be present but email absent
    assert result["resolved_fields"].get("client.full_name") == "Ana Garcia"
    assert "client.email" not in result["resolved_fields"]


async def test_prefill_unknown_form_raises() -> None:
    store = TemplateStore()
    ledger, docstore = _components()
    matter = _matter()
    with pytest.raises(TemplateNotFound):
        await tool_form_prefill(
            matter=matter, form_id="UNKNOWN-999",
            template_store=store, ledger=ledger, docstore=docstore,
        )


# ──────────────────────────────────────────────────────────────
# G7 — PBT properties
# ──────────────────────────────────────────────────────────────

@given(text=st.text(min_size=0, max_size=200))
@settings(max_examples=200)
def test_pbt_no_silent_blank(text: str) -> None:
    """A required var with None always produces a gap placeholder — never empty."""
    var = TemplateVariable(name="x", label="Field X", required=True, type="string")
    spec = TemplateSpec(name="t", version=1, format="docx", variables=[var])
    gap = Gap(variable="x", label="Field X", reason="missing")
    template = b"{{ x }}"
    rendered = render_docx(template, {"x": None}, [gap])
    decoded = rendered.decode("utf-8", errors="replace")
    assert "[[MISSING:" in decoded, "Gap must produce a visible placeholder"
    assert decoded.strip() != "", "Output must not be empty"


@given(
    name=st.text(min_size=1, max_size=20, alphabet=st.characters(blacklist_categories=('Cs',))),
    ver=st.integers(min_value=1, max_value=100),
    ctx=st.fixed_dictionaries({"a": st.text(min_size=0, max_size=50)}),
)
@settings(max_examples=200)
def test_pbt_idem_key_deterministic(name: str, ver: int, ctx: dict) -> None:
    k1 = derive_idem_key(name, ver, ctx)
    k2 = derive_idem_key(name, ver, ctx)
    assert k1 == k2
    assert len(k1) == 24


@given(data=st.binary(min_size=0, max_size=1000))
@settings(max_examples=200)
def test_pbt_checksum_stable(data: bytes) -> None:
    assert checksum(data) == checksum(data)


@given(ver_count=st.integers(min_value=1, max_value=20))
@settings(max_examples=100)
def test_pbt_version_monotonic(ver_count: int) -> None:
    ledger = InMemoryVersionLedger()
    versions = [ledger.next_version("m1", "t", "k") for _ in range(ver_count)]
    for i in range(1, len(versions)):
        assert versions[i] == versions[i - 1] + 1
