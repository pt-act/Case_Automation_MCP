"""Document routing feature-local types — spec §3, §4.1."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RouteOptions(BaseModel):
    force_review: bool = False
    dry_run: bool = False
    recipient_routing_enabled: bool = False


class RouteRequest(BaseModel):
    """Input to the document.route tool."""

    document_id: str
    recipient_id: str | None = None
    options: RouteOptions = Field(default_factory=RouteOptions)


class RoutingDestination(BaseModel):
    kind: Literal["folder", "review_queue"]
    matter_id: str | None = None
    folder: str | None = None
    reason: str | None = None


class GateVerdict(BaseModel):
    verdict: Literal["pass", "warn", "fail"]
    reason: str = ""


class RoutingDecision(BaseModel):
    """Full internal routing decision (stored in routing_decision table)."""

    document_id: str
    idempotency_key: str
    classification: str
    classification_confidence: float
    classification_source: Literal["rule", "extraction", "fallback"]
    name: str
    destination: RoutingDestination
    acl_digest: str
    privileged: bool
    recipient_id: str | None
    gate_verdict: Literal["pass", "warn", "fail"]
    outcome: Literal["moved", "queued", "blocked", "duplicate", "dry_run"]
    routing_intent_version: str = "v1"


class RouteResult(BaseModel):
    """MCP tool output."""

    document_id: str
    classification: str
    classification_confidence: float
    name: str
    destination: RoutingDestination
    privileged: bool
    gate_verdict: str
    outcome: Literal["moved", "queued", "blocked", "duplicate", "dry_run"]
    idempotency_key: str
    audit_id: str = ""
    status: str = "ok"
