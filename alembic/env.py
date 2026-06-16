"""Alembic async environment. The URL comes from AFC_DATABASE_URL only."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import create_engine
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


def _is_sqlite_aiosqlite(url: str) -> bool:
    return url.startswith("sqlite+aiosqlite://")


def _sync_sqlite_url(url: str) -> str:
    return url.replace("sqlite+aiosqlite://", "sqlite://", 1)


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
    url = _database_url()
    if _is_sqlite_aiosqlite(url):
        engine = create_engine(_sync_sqlite_url(url))
        with engine.connect() as connection:
            _run_sync_migrations(connection)
        engine.dispose()
        return

    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync_migrations)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
