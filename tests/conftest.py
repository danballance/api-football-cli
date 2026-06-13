"""Shared fixtures: an aiosqlite database with the full schema."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from api_football_cli.adapters.outbound.persistence.engine import SessionFactory
from api_football_cli.adapters.outbound.persistence.tables import Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
async def sessions() -> AsyncIterator[SessionFactory]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
