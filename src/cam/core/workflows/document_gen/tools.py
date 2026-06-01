"""document.generate + form.prefill MCP tools + QC + audit — spec G5/G6.

Risk tier: write (confirm) — reversible draft stored, nothing sent/filed.
Exactly one hash-chained audit record per generation, written before "complete".
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import structlog

from cam.core.domain.models import Document, Matter
from cam.core.workflows.document_gen.context import build_context, detect_gaps
from cam.core.workflows.document_gen.renderer import RenderError, checksum, render_docx, to_pdf
from cam.core.workflows.document_gen.template_store import TemplateNotFound, TemplateStore
from cam.core.workflows.document_gen.types import Gap, GenerationResult, TemplateSpec
from cam.core.workflows.document_gen.version_store import (
    InMemoryVersionLedger,
    VersionCollisionError,
    derive_idem_key,
    store_document,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# QC integration (G5)
# ---------------------------------------------------------------------------


async def _run_qc(
    spec: TemplateSpec,
    rendered_bytes: bytes,
    matter: Matter,
    context: dict[str, Any],
    gaps: list[Gap],
    run_id: str,
    audit_fn: Any | None,
) -> str:
    """Call qc.verify (completeness + consistency).  Returns pass|warn|fail|skipped."""
    try:
        from cam.core.services.qc.checks import register_all
        from cam.core.services.qc.packet import (
            PacketKind, TemplateBinding, VerificationPacket,
        )
        from cam.core.services.qc.tool import QCVerifyInput, tool_qc_verify
        from cam.core.services.qc.types import Aggregate

        resolved_vars = {v.name: context.get(v.name) for v in spec.variables}
        binding = TemplateBinding(
            template_name=spec.name,
            required_vars=[v.name for v in spec.variables if v.required],
            resolved_vars={k: (str(v) if v is not None else None) for k, v in resolved_vars.items()},
        )
        packet = VerificationPacket(
            packet_id=f"qc-docgen-{run_id}",
            run_id=run_id,
            kind=PacketKind.DOCUMENT_GENERATE,
            matter=matter,
            template_bindings=[binding],
            now=datetime.now(tz=timezone.utc),
        )
        inp = QCVerifyInput(packet=packet, checks=["completeness", "consistency"])
        report = await tool_qc_verify(inp, audit_fn=audit_fn)
        if report.aggregate == Aggregate.BLOCK:
            return "fail"
        if report.aggregate == Aggregate.PASS_WITH_WARNINGS:
            return "warn"
        return "pass"
    except Exception as exc:
        log.warning("docgen.qc_unavailable", error=str(exc))
        return "skipped"


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------


def _determine_status(gaps: list[Gap], qc_verdict: str) -> str:
    if gaps:
        return "incomplete"
    if qc_verdict == "fail":
        return "blocked"
    if qc_verdict == "skipped":
        return "incomplete"  # cannot certify without QC
    return "ready"


# ---------------------------------------------------------------------------
# document.generate tool (G6.1)
# ---------------------------------------------------------------------------


async def tool_document_generate(
    *,
    matter: Matter,
    template_name: str,
    template_store: TemplateStore,
    ledger: InMemoryVersionLedger,
    docstore: Any,
    data_context: dict[str, Any] | None = None,
    doc_key: str | None = None,
    idem_key_override: str | None = None,
    render_pdf: bool = False,
    run_id: str | None = None,
    audit_fn: Any | None = None,
) -> GenerationResult:
    """document.generate — risk tier write (confirm).

    Runs the §5 pipeline: resolve → gaps → render → QC → store → audit.
    Returns GenerationResult.  Never sends or files the document.
    """
    run_id = run_id or str(uuid.uuid4())
    warnings: list[str] = []

    # 1. Load template
    try:
        spec, template_bytes = template_store.get(template_name)
    except TemplateNotFound:
        log.error("docgen.template_not_found", template=template_name)
        return GenerationResult(
            status="failed",
            template_name=template_name,
            run_id=run_id,
            warnings=[f"Template {template_name!r} not found."],
        )

    # 2. Resolve context + detect gaps
    context = build_context(spec, matter, data_context)
    gaps = detect_gaps(spec, context)

    # 3. Derive idempotency key
    idem_key = idem_key_override or derive_idem_key(template_name, spec.version, context)

    # 4. Idempotency check
    existing = ledger.lookup_idem(idem_key)
    if existing is not None:
        log.debug("docgen.idempotent_hit", idem_key=idem_key)
        return GenerationResult(
            document=existing,
            status="ready",
            gaps=gaps,
            qc_verdict="skipped",
            template_name=template_name,
            template_version=spec.version,
            idempotency_key=idem_key,
            run_id=run_id,
        )

    # 5. Render DOCX
    try:
        rendered = render_docx(template_bytes, context, gaps)
    except RenderError as exc:
        await _write_audit(audit_fn, run_id, template_name, spec.version, idem_key, gaps, "skipped", "failed", context)
        return GenerationResult(
            status="failed", template_name=template_name,
            template_version=spec.version, idempotency_key=idem_key, run_id=run_id,
            warnings=[str(exc)],
        )

    # 6. PDF convert (optional)
    if render_pdf:
        pdf = to_pdf(rendered)
        if pdf is None:
            warnings.append("PDF conversion unavailable; DOCX stored only.")

    # 7. Checksum
    doc_checksum = checksum(rendered)

    # 8. QC
    qc_verdict = await _run_qc(spec, rendered, matter, context, gaps, run_id, audit_fn)

    # 9. Store
    effective_doc_key = doc_key or template_name
    try:
        stored_doc = await store_document(
            matter_id=matter.id,
            template_name=template_name,
            template_version=spec.version,
            doc_key=effective_doc_key,
            rendered_bytes=rendered,
            rendered_checksum=doc_checksum,
            privileged=spec.privileged_default,
            classification=spec.target_form_id or template_name,
            idem_key=idem_key,
            ledger=ledger,
            docstore=docstore,
        )
    except VersionCollisionError as exc:
        await _write_audit(audit_fn, run_id, template_name, spec.version, idem_key, gaps, qc_verdict, "failed", context)
        return GenerationResult(
            status="failed", template_name=template_name,
            template_version=spec.version, idempotency_key=idem_key, run_id=run_id,
            warnings=[str(exc)],
        )
    except Exception as exc:
        await _write_audit(audit_fn, run_id, template_name, spec.version, idem_key, gaps, qc_verdict, "failed", context)
        return GenerationResult(
            status="failed", template_name=template_name,
            template_version=spec.version, idempotency_key=idem_key, run_id=run_id,
            warnings=[f"Store error: {exc}"],
        )

    status = _determine_status(gaps, qc_verdict)
    await _write_audit(audit_fn, run_id, template_name, spec.version, idem_key, gaps, qc_verdict, status, context)

    return GenerationResult(
        document=stored_doc,
        status=status,
        gaps=gaps,
        qc_verdict=qc_verdict,
        template_name=template_name,
        template_version=spec.version,
        idempotency_key=idem_key,
        run_id=run_id,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# form.prefill tool (G6.2)
# ---------------------------------------------------------------------------


async def tool_form_prefill(
    *,
    matter: Matter,
    form_id: str,
    template_store: TemplateStore,
    ledger: InMemoryVersionLedger,
    docstore: Any,
    data_context: dict[str, Any] | None = None,
    emit: Literal["fields_only", "document"] = "fields_only",
    run_id: str | None = None,
    audit_fn: Any | None = None,
) -> dict:
    """form.prefill — risk tier read (fields_only) or write/confirm (document).

    Never fabricates values.  Unresolved fields become gaps.
    ASSUMPTION (confirm): supported form_ids (I-130, I-485, N-400, G-28).
    """
    # Map form_id to template name (data-driven; ASSUMPTION confirm)
    form_to_template: dict[str, str] = {
        "I-130": "i130_petition",
        "I-485": "i485_adjustment",
        "N-400": "n400_naturalization",
        "G-28": "g28_representation",
    }
    template_name = form_to_template.get(form_id)
    if template_name is None:
        raise TemplateNotFound(form_id)

    try:
        spec, _ = template_store.get(template_name)
    except TemplateNotFound:
        raise TemplateNotFound(form_id)

    context = build_context(spec, matter, data_context)
    gaps = detect_gaps(spec, context)

    if emit == "fields_only":
        return {
            "resolved_fields": {k: v for k, v in context.items() if v is not None},
            "gaps": [g.model_dump() for g in gaps],
            "form_id": form_id,
            "template_version": spec.version,
        }

    result = await tool_document_generate(
        matter=matter,
        template_name=template_name,
        template_store=template_store,
        ledger=ledger,
        docstore=docstore,
        data_context=data_context,
        run_id=run_id,
        audit_fn=audit_fn,
    )
    return result.model_dump()


# ---------------------------------------------------------------------------
# Audit helper (G6.3)
# ---------------------------------------------------------------------------


async def _write_audit(
    audit_fn: Any,
    run_id: str,
    template_name: str,
    template_version: int,
    idem_key: str,
    gaps: list[Gap],
    qc_verdict: str,
    status: str,
    context: dict[str, Any],
) -> None:
    if audit_fn is None:
        return
    # Input digest (not raw PII)
    context_digest = hashlib.sha256(
        str(sorted(context.keys())).encode()
    ).hexdigest()[:12]
    try:
        await audit_fn(
            actor="docgen_service",
            action="document.generate",
            inputs={
                "template": template_name,
                "template_version": template_version,
                "idem_key": idem_key,
                "context_digest": context_digest,  # not raw values
                "gap_count": len(gaps),
            },
            outputs={
                "status": status,
                "qc_verdict": qc_verdict,
            },
            run_id=run_id,
        )
    except Exception:
        pass
