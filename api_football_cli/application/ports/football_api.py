"""The FootballApi port (architecture §4)."""

from __future__ import annotations

from typing import Protocol

from api_football_cli.domain.entities import (
    AccountStatus,
    FixtureSnapshot,
    LeagueWithSeasons,
    ObservedEvent,
    TeamWithVenue,
)


class ApiFootballError(RuntimeError):
    """Any upstream failure, including errors reported inside an HTTP 200."""


class FootballApi(Protocol):
    """Driven port for api-football v3."""

    async def fixture(self, api_fixture_id: int) -> FixtureSnapshot: ...

    async def fixtures_events(self, api_fixture_id: int) -> list[ObservedEvent]: ...

    async def leagues(self, *, season: int) -> list[LeagueWithSeasons]: ...

    async def teams(self, *, league_api_id: int, season: int) -> list[TeamWithVenue]: ...

    async def fixtures_by_league(
        self, *, league_api_id: int, season: int
    ) -> list[FixtureSnapshot]: ...

    async def account_status(self) -> AccountStatus: ...

    def requests_remaining(self) -> int | None:
        """Daily quota left per the most recent response; None when unknown."""
        ...
