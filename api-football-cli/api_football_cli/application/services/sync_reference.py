"""SyncReferenceData: one-shot crawls of static reference data (architecture §1).

Each method performs one api-football call and upserts the results keyed on
their api_*_id reference columns. These run as separate CLI commands, outside
the live runtime.
"""

from __future__ import annotations

from api_football_cli.application.ports.football_api import FootballApi
from api_football_cli.application.ports.repositories import (
    FixtureRepository,
    ReferenceRepository,
)
from api_football_cli.domain.entities import FrozenModel


class LeagueSyncReport(FrozenModel):
    leagues: int
    seasons: int


class TeamSyncReport(FrozenModel):
    teams: int
    venues: int


class FixtureSyncReport(FrozenModel):
    fixtures: int


class SyncReferenceData:
    def __init__(
        self,
        *,
        api: FootballApi,
        reference: ReferenceRepository,
        fixtures: FixtureRepository,
    ) -> None:
        self._api = api
        self._reference = reference
        self._fixtures = fixtures

    async def sync_leagues(self, *, season: int) -> LeagueSyncReport:
        league_count = 0
        season_count = 0
        for item in await self._api.leagues(season=season):
            league_id = await self._reference.upsert_league(item.league)
            league_count += 1
            for league_season in item.seasons:
                await self._reference.upsert_season(league_id=league_id, season=league_season)
                season_count += 1
        return LeagueSyncReport(leagues=league_count, seasons=season_count)

    async def sync_teams(self, *, league_api_id: int, season: int) -> TeamSyncReport:
        team_count = 0
        venue_count = 0
        for item in await self._api.teams(league_api_id=league_api_id, season=season):
            venue_id: int | None = None
            if item.venue is not None:
                venue_id = await self._reference.upsert_venue(item.venue)
                venue_count += 1
            await self._reference.upsert_team(team=item.team, venue_id=venue_id)
            team_count += 1
        return TeamSyncReport(teams=team_count, venues=venue_count)

    async def sync_fixtures(self, *, league_api_id: int, season: int) -> FixtureSyncReport:
        count = 0
        for snapshot in await self._api.fixtures_by_league(
            league_api_id=league_api_id, season=season
        ):
            await self._fixtures.upsert_snapshot(snapshot)
            count += 1
        return FixtureSyncReport(fixtures=count)
