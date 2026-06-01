"""Alembic environment — wired to the ORM metadata."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from cam.persistence.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Allow DATABASE_URL env override (for CI / tests)
_db_url = os.environ.get("CAM_DATABASE_URL") or os.environ.get("CAM_MIGRATION_URL")
if _db_url:
    # Alembic migrations always run synchronously; convert async URL if needed.
    _sync_url = _db_url.replace("+asyncpg", "+psycopg2").replace("postgresql+psycopg2", "postgresql")
    # keep postgresql+psycopg2 as-is; strip asyncpg
    _sync_url = _db_url.replace("+asyncpg", "")
    config.set_main_option("sqlalchemy.url", _sync_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
