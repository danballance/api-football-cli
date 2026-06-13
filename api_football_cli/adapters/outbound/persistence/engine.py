"""Async engine / session factory construction."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

SessionFactory = async_sessionmaker[AsyncSession]


def create_engine_and_sessions(url: str) -> tuple[AsyncEngine, SessionFactory]:
    engine = create_async_engine(url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
