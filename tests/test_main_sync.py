"""Composition-root sync runners over a real (SQLite) database."""

from __future__ import annotations

from pathlib import Path

from api_football_cli.adapters.outbound.persistence.engine import create_engine_and_sessions
from api_football_cli.adapters.outbound.persistence.tables import Base
from api_football_cli.main import run_sync_fixtures, run_sync_leagues, run_sync_teams
from tests.factories import make_snapshot
from tests.fakes import StubFootballApi
from tests.test_sync_service import LEAGUES, TEAMS


async def prepare_database(tmp_path: Path) -> str:
    url = f"sqlite+aiosqlite:///{tmp_path}/sync.db"
    engine, _ = create_engine_and_sessions(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return url


async def test_run_sync_leagues(tmp_path: Path) -> None:
    url = await prepare_database(tmp_path)
    api = StubFootballApi(snapshots=[], event_batches=[], remaining=None, leagues=LEAGUES)
    report = await run_sync_leagues(api=api, database_url=url, season=2025)
    assert (report.leagues, report.seasons) == (1, 2)


async def test_run_sync_teams(tmp_path: Path) -> None:
    url = await prepare_database(tmp_path)
    api = StubFootballApi(snapshots=[], event_batches=[], remaining=None, teams=TEAMS)
    report = await run_sync_teams(api=api, database_url=url, league_api_id=9990, season=2025)
    assert (report.teams, report.venues) == (2, 1)


async def test_run_sync_fixtures(tmp_path: Path) -> None:
    url = await prepare_database(tmp_path)
    api = StubFootballApi(
        snapshots=[],
        event_batches=[],
        remaining=None,
        league_fixtures=[make_snapshot(api_fixture_id=1001)],
    )
    report = await run_sync_fixtures(api=api, database_url=url, league_api_id=9990, season=2025)
    assert report.fixtures == 1
