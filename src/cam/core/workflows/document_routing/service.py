"""RoutingService — orchestration + idempotency + DocStore move — spec G6/G7/G8.

Flow: classify → resolve → name → acl → privilege gate → (move | queue | block | dup)
Privilege gate precedes ALL external effects.
Idempotency key prevents double-filing.
Audit written before returning.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from cam.core.domain.models import ACL, Document, Matter
from cam.core.workflows.document_routing.acl_builder import AclBuilder
from cam.core.workflows.document_routing.classifier import Classifier
from cam.core.workflows.document_routing.config import RoutingConfig, get_routing_config
from cam.core.workflows.document_routing.namer import Namer, Resolver
from cam.core.workflows.document_routing.privilege_gate import PrivilegeGate
from cam.core.workflows.document_routing.types import (
    RouteOptions,
    RouteResult,
    RoutingDecision,
    RoutingDestination,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Idempotency store (in-memory for tests)
# ---------------------------------------------------------------------------


class RoutingDecisionStore:
    """Stores routing decisions for idempotency and audit."""

    def __init__(self) -> None:
        self._decisions: dict[str, RoutingDecision] = {}

    def lookup(self, idem_key: str) -> RoutingDecision | None:
        return self._decisions.get(idem_key)

    def save(self, decision: RoutingDecision) -> None:
        self._decisions[decision.idempotency_key] = decision

    def all_decisions(self) -> list[RoutingDecision]:
        return list(self._decisions.values())


def _derive_idem_key(
    document_id: str,
    checksum: str,
    recipient_id: str | None,
    routing_intent_version: str,
) -> str:
    raw = f"{document_id}:{checksum}:{recipient_id or ''}:{routing_intent_version}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# RoutingService
# ---------------------------------------------------------------------------


class RoutingService:
    """Orchestrates the full document routing pipeline."""

    def __init__(
        self,
        decision_store: RoutingDecisionStore,
        docstore: Any,          # DocStoreConnector port
        config: RoutingConfig | None = None,
        audit_fn: Any | None = None,
    ) -> None:
        self._store = decision_store
        self._docstore = docstore
        self._config = config or get_routing_config()
        self._audit = audit_fn
        self._classifier = Classifier()
        self._namer = Namer()
        self._resolver = Resolver()
        self._acl_builder = AclBuilder()
        self._gate = PrivilegeGate()

    async def route(
        self,
        document: Document,
        matter: Matter,
        options: RouteOptions,
        extraction_fields: list[dict] | None = None,
        recipient_id: str | None = None,
        run_id: str | None = None,
    ) -> RouteResult:
        """Route a document to its canonical destination."""
        run_id = run_id or str(uuid.uuid4())
        cfg = self._config

        # 1. Derive idempotency key
        idem_key = _derive_idem_key(
            document.id, document.checksum,
            recipient_id, cfg.routing_intent_version,
        )

        # 2. Idempotency check
        existing = self._store.lookup(idem_key)
        if existing is not None:
            log.debug("routing.duplicate", idem_key=idem_key)
            result = _decision_to_result(existing, "duplicate", idem_key)
            await self._write_audit(run_id, document, existing, "duplicate")
            return result

        # 3. Classify
        class_name, confidence, source = self._classifier.classify(
            document, extraction_fields, cfg
        )
        low_confidence = (
            class_name == "unknown" or confidence < cfg.classification_threshold
        )

        # 4. Resolve destination
        destination = self._resolver.resolve(
            document, class_name, matter,
            force_review=options.force_review or low_confidence,
            config=cfg,
        )

        # 5. Derive name
        doc_name = self._namer.derive_name(document, class_name, matter, cfg)

        # 6. Build ACL
        acl = self._acl_builder.build(matter, class_name, cfg)
        acl_digest = hashlib.sha256(str(sorted(acl.principals)).encode()).hexdigest()[:12]

        # 7. Privilege gate — before ANY external effect (non-overridable)
        gate_verdict = self._gate.check(
            document, destination, recipient_id,
            caller_claims_external=(recipient_id is not None),
        )
        if gate_verdict.verdict == "fail":
            decision = RoutingDecision(
                document_id=document.id,
                idempotency_key=idem_key,
                classification=class_name,
                classification_confidence=confidence,
                classification_source=source,
                name=doc_name,
                destination=destination,
                acl_digest=acl_digest,
                privileged=document.privileged,
                recipient_id=recipient_id,
                gate_verdict="fail",
                outcome="blocked",
                routing_intent_version=cfg.routing_intent_version,
            )
            self._store.save(decision)
            await self._write_audit(run_id, document, decision, "blocked")
            log.warning("routing.blocked", document_id=document.id, reason=gate_verdict.reason)
            return _decision_to_result(decision, "blocked", idem_key)

        # 8. Queue if review destination
        if destination.kind == "review_queue":
            decision = RoutingDecision(
                document_id=document.id,
                idempotency_key=idem_key,
                classification=class_name,
                classification_confidence=confidence,
                classification_source=source,
                name=doc_name,
                destination=destination,
                acl_digest=acl_digest,
                privileged=document.privileged,
                recipient_id=recipient_id,
                gate_verdict=gate_verdict.verdict,
                outcome="queued",
                routing_intent_version=cfg.routing_intent_version,
            )
            self._store.save(decision)
            await self._write_audit(run_id, document, decision, "queued")
            return _decision_to_result(decision, "queued", idem_key)

        # 9. Dry run — compute decision, skip move
        if options.dry_run:
            decision = RoutingDecision(
                document_id=document.id,
                idempotency_key=idem_key,
                classification=class_name,
                classification_confidence=confidence,
                classification_source=source,
                name=doc_name,
                destination=destination,
                acl_digest=acl_digest,
                privileged=document.privileged,
                recipient_id=recipient_id,
                gate_verdict=gate_verdict.verdict,
                outcome="dry_run",
                routing_intent_version=cfg.routing_intent_version,
            )
            self._store.save(decision)
            await self._write_audit(run_id, document, decision, "dry_run")
            return _decision_to_result(decision, "dry_run", idem_key)

        # 10. Move via DocStoreConnector
        try:
            await self._docstore.move(
                document.id,
                destination.folder or "review_queue",
                acl,
            )
            outcome = "moved"
        except Exception as exc:
            from cam.connectors.errors import classify as classify_error, FatalError
            err = classify_error(exc, "docstore")
            log.error("routing.move_failed", error=str(err), document_id=document.id)
            outcome = "blocked"

        decision = RoutingDecision(
            document_id=document.id,
            idempotency_key=idem_key,
            classification=class_name,
            classification_confidence=confidence,
            classification_source=source,
            name=doc_name,
            destination=destination,
            acl_digest=acl_digest,
            privileged=document.privileged,
            recipient_id=recipient_id,
            gate_verdict=gate_verdict.verdict,
            outcome=outcome,
            routing_intent_version=cfg.routing_intent_version,
        )
        self._store.save(decision)
        await self._write_audit(run_id, document, decision, outcome)
        log.info("routing.complete", document_id=document.id, outcome=outcome,
                 class_name=class_name, destination=destination.kind)
        return _decision_to_result(decision, outcome, idem_key)

    async def _write_audit(
        self,
        run_id: str,
        document: Document,
        decision: RoutingDecision,
        outcome: str,
    ) -> None:
        if self._audit is None:
            return
        try:
            await self._audit(
                actor="document_routing_service",
                action="document.route",
                inputs={
                    "document_id": document.id,
                    "idem_key": decision.idempotency_key,
                    "classification": decision.classification,
                    "destination_kind": decision.destination.kind,
                },
                outputs={
                    "outcome": outcome,
                    "gate_verdict": decision.gate_verdict,
                    "acl_digest": decision.acl_digest,
                },
                run_id=run_id,
            )
        except Exception:
            pass


def _decision_to_result(
    d: RoutingDecision,
    outcome: str,
    idem_key: str,
) -> RouteResult:
    return RouteResult(
        document_id=d.document_id,
        classification=d.classification,
        classification_confidence=d.classification_confidence,
        name=d.name,
        destination=d.destination,
        privileged=d.privileged,
        gate_verdict=d.gate_verdict,
        outcome=outcome,
        idempotency_key=idem_key,
    )
