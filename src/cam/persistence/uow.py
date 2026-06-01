"""Unit of Work / session scope — transactional grouping for write-before-complete.

The UoW ensures that an action and its audit record commit atomically.
If the audit write fails, the action rolls back — never a silent gap (spec §5.2).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_session_factory: async_sessionmaker[AsyncSession] | None = None


def configure_db(database_url: str, pool_size: int = 10, max_overflow: int = 20) -> None:
    """Initialise the async engine and session factory.  Call once at startup."""
    global _session_factory
    engine = create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        echo=False,
    )
    _session_factory = async_sessionmaker(engine, expire_on_commit=False)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database not configured.  Call configure_db() at startup.")
    return _session_factory


@asynccontextmanager
async def unit_of_work() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager providing a single transactional session.

    Usage::

        async with unit_of_work() as session:
            session.add(orm_object)
            audit_service.record(session, ...)  # same transaction
        # committed atomically on exit, rolled back on exception
    """
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            yield session
