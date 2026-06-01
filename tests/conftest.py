"""Shared pytest fixtures for platform-foundation tests."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cam.persistence.models import Base
from cam.security.crypto import CryptoService, configure_crypto

# ---------------------------------------------------------------------------
# Encryption fixture — 32-byte test KEK
# ---------------------------------------------------------------------------

TEST_KEK = b"A" * 32  # 256-bit test key; never use in production


@pytest.fixture(autouse=True)
def _setup_crypto() -> None:
    """Configure a test CryptoService for every test."""
    configure_crypto(TEST_KEK)


# ---------------------------------------------------------------------------
# Global singleton reset — prevents state leakage between test files.
# Module-level mutable singletons must be reset so each test starts clean.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_global_singletons() -> None:
    """Reset all module-level service singletons before each test."""
    # Observability — prevent structlog re-configuration bleed
    import cam.obs.observability as obs_mod
    obs_mod._is_configured = False

    # Workflow service containers
    import cam.core.workflows.intake.workflow as intake_wf
    intake_wf._services = None

    import cam.core.workflows.status_update.workflow as su_wf
    su_wf._services = None

    # Orchestrator DSL registry (tests that don't have their own autouse fixture)
    # Note: individual test files that use @workflow should also call clear_registry()
    # via their own autouse fixtures; this is a safety net only.

    # DB session factory (so tests that set it don't bleed into unrelated tests)
    import cam.persistence.uow as uow_mod
    uow_mod._session_factory = None

    # Config singletons — reset to defaults so set_intake_config()/set_routing_config()
    # calls in one test don't affect another.
    import cam.core.workflows.intake.config as intake_cfg
    intake_cfg._config = intake_cfg.IntakeConfig()

    import cam.core.workflows.document_routing.config as routing_cfg
    routing_cfg._config = routing_cfg.RoutingConfig()


# ---------------------------------------------------------------------------
# In-process async engine + session for DB tests
# ---------------------------------------------------------------------------

TEST_DB_URL = os.environ.get(
    "CAM_DATABASE_URL",
    "postgresql+asyncpg://cam:cam_test_pw@localhost:5432/cam_test",
)


@pytest_asyncio.fixture()
async def db_engine():  # type: ignore[return]
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture()
async def db_session(db_engine) -> AsyncSession:  # type: ignore[return]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            yield session
