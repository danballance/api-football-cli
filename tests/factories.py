"""Builders for domain objects used across the suite."""

from __future__ import annotations

from datetime import UTC, datetime

from api_football_cli.domain.entities import (
    Commentator,
    CommentatorRole,
    CommentatorStyle,
    FixtureSnapshot,
    FixtureStatus,
    LeagueRef,
    ObservedEvent,
    PlayerRef,
    TeamRef,
)

HOME = TeamRef(api_team_id=501, name="Riverton Albion")
AWAY = TeamRef(api_team_id=502, name="Kingsport Wanderers")
SCORER = PlayerRef(api_player_id=70001, name="D. Mercer")
LEAGUE = LeagueRef(api_league_id=9990, name="Demo Premier League", country="Demoland", season=2025)
KICKOFF = datetime(2025, 8, 16, 14, 0, tzinfo=UTC)


def make_snapshot(
    *,
    api_fixture_id: int = 1001,
    status: FixtureStatus = FixtureStatus.NOT_STARTED,
    elapsed: int | None = None,
    home_goals: int | None = None,
    away_goals: int | None = None,
) -> FixtureSnapshot:
    return FixtureSnapshot(
        api_fixture_id=api_fixture_id,
        league=LEAGUE,
        kickoff=KICKOFF,
        status=status,
        elapsed=elapsed,
        home=HOME,
        away=AWAY,
        home_goals=home_goals,
        away_goals=away_goals,
        referee="A. Whistler",
    )


def make_event(
    *,
    elapsed: int = 10,
    extra: int | None = None,
    team: TeamRef = HOME,
    player: PlayerRef | None = SCORER,
    assist: PlayerRef | None = None,
    type_: str = "Goal",
    detail: str = "Normal Goal",
    comments: str | None = None,
) -> ObservedEvent:
    return ObservedEvent(
        elapsed=elapsed,
        extra=extra,
        team=team,
        player=player,
        assist=assist,
        type=type_,
        detail=detail,
        comments=comments,
    )


def make_commentator(
    *, id: int = 1, role: CommentatorRole = CommentatorRole.PLAY_BY_PLAY, name: str = "Marty Vox"
) -> Commentator:
    return Commentator(
        id=id,
        name=name,
        role=role,
        system_prompt=f"You are {name}.",
        style=CommentatorStyle(tone="test", quirks=("quirk",)),
    )


BOOTH = (
    make_commentator(id=1, role=CommentatorRole.PLAY_BY_PLAY, name="Marty Vox"),
    make_commentator(id=2, role=CommentatorRole.COLOR, name="Robbie Banks"),
)
