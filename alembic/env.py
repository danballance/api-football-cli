"""Alembic async environment. The URL comes from AFC_DATABASE_URL only."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from api_football_cli.adapters.outbound.persistence.tables import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("AFC_DATABASE_URL")
    if url is None or url.strip() == "":
        raise RuntimeError("environment variable AFC_DATABASE_URL must be set for migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync_migrations)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
