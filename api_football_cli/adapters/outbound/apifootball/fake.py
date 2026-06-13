"""FakeFootballApi: replay adapter for dev, CI and demos (architecture §10).

A recorded finished fixture is replayed on an accelerated clock: every
``fixture()`` call advances the simulated match by ``minutes_per_poll`` and
``fixtures_events()`` returns the cumulative feed up to that minute — exactly
how the real endpoint behaves, so ingestion is identical in live and replay.
"""

from __future__ import annotations

from pathlib import Path

from api_football_cli.application.ports.football_api import ApiFootballError, FootballApi
from api_football_cli.domain.entities import (
    AccountStatus,
    FixtureSnapshot,
    FixtureStatus,
    FrozenModel,
    LeagueWithSeasons,
    ObservedEvent,
    TeamWithVenue,
)
from api_football_cli.domain.events import is_scoring_goal

HALF_TIME_MINUTE = 45
FULL_TIME_MINUTE = 90


class ReplayFile(FrozenModel):
    """Serialized recording: the fixture's metadata plus its full event list."""

    fixture: FixtureSnapshot
    events: tuple[ObservedEvent, ...]

    @classmethod
    def load(cls, path: Path) -> ReplayFile:
        if not path.is_file():
            raise ApiFootballError(f"replay file not found: {path}")
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def dump(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")


class FakeFootballApi(FootballApi):
    """Implements the live-flow endpoints of FootballApi from a recording."""

    def __init__(self, *, replay: ReplayFile, minutes_per_poll: int) -> None:
        if minutes_per_poll < 1:
            raise ApiFootballError(f"minutes_per_poll must be >= 1, got {minutes_per_poll}")
        self._replay = replay
        self._minutes_per_poll = minutes_per_poll
        self._minute = 0
        last_event_minute = max((e.elapsed for e in replay.events), default=0)
        self._end_minute = max(FULL_TIME_MINUTE, last_event_minute)

    @property
    def minute(self) -> int:
        return self._minute

    def _require_fixture(self, api_fixture_id: int) -> None:
        expected = self._replay.fixture.api_fixture_id
        if api_fixture_id != expected:
            raise ApiFootballError(
                f"replay file holds fixture {expected}, not {api_fixture_id}"
            )

    async def fixture(self, api_fixture_id: int) -> FixtureSnapshot:
        self._require_fixture(api_fixture_id)
        self._minute = min(self._minute + self._minutes_per_poll, self._end_minute)

        if self._minute >= self._end_minute:
            status = FixtureStatus.FULL_TIME
        elif self._minute <= HALF_TIME_MINUTE:
            status = FixtureStatus.FIRST_HALF
        else:
            status = FixtureStatus.SECOND_HALF

        home_goals, away_goals = self._score()
        return self._replay.fixture.model_copy(
            update={
                "status": status,
                "elapsed": min(self._minute, FULL_TIME_MINUTE),
                "home_goals": home_goals,
                "away_goals": away_goals,
            }
        )

    def _score(self) -> tuple[int, int]:
        # Naive scoreboard: VAR cancellations are not subtracted. Replay drives
        # the commentary pipeline; the authoritative score lives on the feed.
        home = self._replay.fixture.home.api_team_id
        visible = (e for e in self._replay.events if e.elapsed <= self._minute)
        goals = [e for e in visible if is_scoring_goal(e)]
        home_goals = sum(1 for e in goals if e.team.api_team_id == home)
        return home_goals, len(goals) - home_goals

    async def fixtures_events(self, api_fixture_id: int) -> list[ObservedEvent]:
        self._require_fixture(api_fixture_id)
        return [e for e in self._replay.events if e.elapsed <= self._minute]

    def requests_remaining(self) -> int | None:
        return None

    async def leagues(self, *, season: int) -> list[LeagueWithSeasons]:
        raise ApiFootballError("replay mode supports only fixture() and fixtures_events()")

    async def teams(self, *, league_api_id: int, season: int) -> list[TeamWithVenue]:
        raise ApiFootballError("replay mode supports only fixture() and fixtures_events()")

    async def fixtures_by_league(
        self, *, league_api_id: int, season: int
    ) -> list[FixtureSnapshot]:
        raise ApiFootballError("replay mode supports only fixture() and fixtures_events()")

    async def account_status(self) -> AccountStatus:
        raise ApiFootballError("replay mode supports only fixture() and fixtures_events()")
