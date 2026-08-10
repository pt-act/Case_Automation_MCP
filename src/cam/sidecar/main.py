"""Case Automation MCP — sidecar FastAPI application.

Exposes:
  POST /webhooks/{connector}   — inbound vendor events (verify → normalise → enqueue)
  GET  /approvals/{token}      — human approval UI page
  POST /approvals/{token}      — submit approval/rejection decision
  GET  /health                 — liveness probe

Usage:
  uvicorn cam.sidecar.main:app --host 0.0.0.0 --port 8001

The MCP server (stdio/SSE) is a separate process; see concept/PTD.md §14.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from cam.sidecar.approvals import router as approvals_router
from cam.sidecar.webhooks import router as webhooks_router

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bootstrap services on startup; tear down on shutdown."""
    log.info("sidecar.starting")

    # 1. Configure observability
    try:
        from cam.config.settings import Settings
        from cam.obs.observability import configure_observability
        settings = Settings.model_validate({})
        configure_observability(
            service_name=settings.service_name,
            log_level=settings.log_level,
            otel_endpoint=settings.otel_exporter_endpoint,
        )
    except Exception as exc:
        logging.warning("Observability config failed (non-fatal): %s", exc)

    # 1b. Select the active domain pack (CAM_DOMAIN_PACK — no implicit default,
    #     decision D-5) and compose the PII redaction set from it (baseline ∪ pack).
    try:
        from cam.config.settings import Settings
        from cam.obs.observability import configure_pii_patterns
        from cam.packs import activate_configured_pack, apply_pack

        settings = Settings.model_validate({})
        pack = activate_configured_pack(settings.domain_pack)
        apply_pack(pack)          # populate intake/routing runtime config from the pack
        configure_pii_patterns()  # compose PII redaction set (baseline ∪ pack)
        log.info("sidecar.domain_pack_ready", pack=pack.name, version=pack.version)
    except Exception as exc:
        # No pack selected / invalid pack is a refuse-to-serve condition; surface
        # it loudly. (Startup remains non-aborting here to match the other steps;
        # hardening to a hard abort is tracked in the domain-packs spec.)
        log.error("sidecar.domain_pack_init_failed", error=str(exc))

    # 2. Configure crypto (KEK from secret store)
    try:
        import base64

        from cam.config.settings import Settings, build_secret_loader
        from cam.security.crypto import configure_crypto
        settings = Settings.model_validate({})
        loader = build_secret_loader(settings)
        kek_b64 = loader.get_secret(settings.encryption_kek_secret_name)
        kek = base64.b64decode(kek_b64)
        configure_crypto(kek)
        log.info("sidecar.crypto_ready")
    except Exception as exc:
        log.error("sidecar.crypto_init_failed", error=str(exc))
        # Don't abort startup — crypto errors will surface at first use

    # 3. Connector registry — register reference adapters for dev/test environments
    # Production: replace with vendor adapters once vendors are confirmed (PRD §11 Q1)
    try:
        from cam.connectors.reference import register_reference_adapters
        register_reference_adapters()
        log.info("sidecar.connectors_registered")
    except Exception as exc:
        log.warning("sidecar.connector_registration_failed", error=str(exc))

    # 4. Database — ensure session factory is configured
    try:
        from cam.config.settings import Settings
        from cam.persistence.uow import configure_db
        settings = Settings.model_validate({})
        configure_db(
            settings.database_url.get_secret_value(),
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
        )
        log.info("sidecar.db_ready")
    except Exception as exc:
        log.warning("sidecar.db_init_failed", error=str(exc))

    # 5. Signing key for approval tokens
    try:
        import base64
        import os
        raw_kek = os.environ.get("CAM_ENCRYPTION_KEK", "")
        app.state.signing_key = base64.b64decode(raw_kek) if raw_kek else b"\x00" * 32
    except Exception:
        app.state.signing_key = b"\x00" * 32

    # 6. Webhook secrets and mocks (replaced by real connectors at production)
    app.state.webhook_secrets = {}    # connector_name → HMAC secret
    app.state.redis_client = None     # injected by production bootstrap
    app.state.trigger_sink = _NoopTriggerSink()
    app.state.run_store = None

    log.info("sidecar.ready")
    yield

    log.info("sidecar.shutdown")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Separate from module-level `app` so tests can create isolated instances.
    """
    application = FastAPI(
        title="Case Automation MCP — Sidecar",
        description=(
            "Webhook ingestion, human approval UI, and scheduler callbacks "
            "for the Case Automation MCP server."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    application.include_router(webhooks_router)
    application.include_router(approvals_router)

    @application.get("/health", tags=["ops"])
    async def health() -> JSONResponse:
        """Liveness probe — returns 200 when the sidecar is running."""
        return JSONResponse({"status": "ok", "service": "cam-sidecar"})

    return application


# Module-level app instance (used by uvicorn)
app: FastAPI = create_app()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NoopTriggerSink:
    """Stub trigger sink — replaced by the real orchestrator at startup."""

    async def enqueue(self, event: Any) -> str:
        log.warning("trigger_sink.noop", event_type=getattr(event, "type", "unknown"))
        return "noop"
