"""SQLAlchemy repositories against an in-memory SQLite database."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from api_football_cli.adapters.outbound.persistence.engine import SessionFactory
from api_football_cli.adapters.outbound.persistence.repositories import (
    SqlApiRequestLogRepository,
    SqlCommentaryRepository,
    SqlCommentatorRepository,
    SqlEventRepository,
    SqlFixtureRepository,
    SqlReferenceRepository,
)
from api_football_cli.adapters.outbound.persistence.tables import ApiRequestLogRow, TeamRow
from api_football_cli.application.ports.repositories import NotFoundError
from api_football_cli.domain.entities import (
    CommentaryDraft,
    CommentatorRole,
    Country,
    Coverage,
    FixtureStatus,
    League,
    PlayerRef,
    Season,
    TeamProfile,
    Venue,
)
from api_football_cli.domain.personas import PERSONAS
from tests.factories import AWAY, make_event, make_snapshot

COVERAGE = Coverage(
    events=True,
    lineups=False,
    statistics_fixtures=False,
    statistics_players=False,
    standings=True,
    players=False,
    top_scorers=False,
    top_assists=False,
    top_cards=False,
    injuries=False,
    predictions=False,
    odds=False,
)


async def test_fixture_upsert_creates_then_updates(sessions: SessionFactory) -> None:
    repo = SqlFixtureRepository(sessions)

    created = await repo.upsert_snapshot(make_snapshot())
    assert created.status is FixtureStatus.NOT_STARTED
    assert created.league.name == "Demo Premier League"
    assert created.league.country == "Demoland"
    assert created.home.name == "Riverton Albion"

    updated = await repo.upsert_snapshot(
        make_snapshot(status=FixtureStatus.SECOND_HALF, elapsed=67, home_goals=2, away_goals=1)
    )
    assert updated.id == created.id
    assert updated.status is FixtureStatus.SECOND_HALF
    assert (updated.home_goals, updated.away_goals) == (2, 1)

    listed = await repo.list_all()
    assert [fixture.id for fixture in listed] == [created.id]

    fetched = await repo.get(created.id)
    assert fetched == updated


async def test_fixture_get_missing_raises(sessions: SessionFactory) -> None:
    repo = SqlFixtureRepository(sessions)
    with pytest.raises(NotFoundError, match="999"):
        await repo.get(999)


async def test_event_append_only_dedup_and_listing(sessions: SessionFactory) -> None:
    fixtures = SqlFixtureRepository(sessions)
    events = SqlEventRepository(sessions)
    fixture = await fixtures.upsert_snapshot(make_snapshot())

    goal = make_event(assist=PlayerRef(api_player_id=70002, name="L. Carter"))
    stored = await events.insert_if_new(fixture_id=fixture.id, event=goal)
    assert stored is not None
    assert stored.event.assist is not None

    duplicate = await events.insert_if_new(fixture_id=fixture.id, event=goal)
    assert duplicate is None

    # VAR correction: different detail -> different hash -> appended.
    correction = make_event(detail="Goal Disallowed - offside")
    appended = await events.insert_if_new(fixture_id=fixture.id, event=correction)
    assert appended is not None

    anonymous = make_event(elapsed=70, player=None, type_="Var", detail="Goal cancelled")
    await events.insert_if_new(fixture_id=fixture.id, event=anonymous)

    all_events = await events.list_for_fixture(fixture.id)
    assert [e.id for e in all_events] == [stored.id, appended.id, appended.id + 1]
    assert all_events[0].event == goal
    assert all_events[2].event.player is None

    tail = await events.list_after(fixture_id=fixture.id, after_event_id=stored.id)
    assert [e.id for e in tail] == [appended.id, appended.id + 1]


async def test_event_insert_updates_player_name(sessions: SessionFactory) -> None:
    fixtures = SqlFixtureRepository(sessions)
    events = SqlEventRepository(sessions)
    fixture = await fixtures.upsert_snapshot(make_snapshot())

    await events.insert_if_new(
        fixture_id=fixture.id,
        event=make_event(player=PlayerRef(api_player_id=70001, name="D Mercer")),
    )
    await events.insert_if_new(
        fixture_id=fixture.id,
        event=make_event(
            elapsed=20, player=PlayerRef(api_player_id=70001, name="Danny Mercer")
        ),
    )
    listed = await events.list_for_fixture(fixture.id)
    player = listed[1].event.player
    assert player is not None and player.name == "Danny Mercer"


async def test_commentator_upsert_and_list(sessions: SessionFactory) -> None:
    repo = SqlCommentatorRepository(sessions)
    first = await repo.upsert(PERSONAS[0])
    again = await repo.upsert(PERSONAS[0])
    assert first.id == again.id
    second = await repo.upsert(PERSONAS[1])

    listed = await repo.list_all()
    assert [c.id for c in listed] == [first.id, second.id]
    assert listed[0].role is CommentatorRole.PLAY_BY_PLAY
    assert listed[1].role is CommentatorRole.COLOR
    assert listed[0].style.quirks


async def test_commentary_insert_list_and_last_trigger(sessions: SessionFactory) -> None:
    fixtures = SqlFixtureRepository(sessions)
    events = SqlEventRepository(sessions)
    commentators = SqlCommentatorRepository(sessions)
    commentary = SqlCommentaryRepository(sessions)

    fixture = await fixtures.upsert_snapshot(make_snapshot())
    stored = await events.insert_if_new(fixture_id=fixture.id, event=make_event())
    assert stored is not None
    voice = await commentators.upsert(PERSONAS[0])

    assert await commentary.last_triggering_event_id(fixture.id) == 0

    first = await commentary.insert(
        CommentaryDraft(
            fixture_id=fixture.id,
            commentator_id=voice.id,
            text="What a strike!",
            triggering_event_id=stored.id,
            in_reply_to=None,
            provider="fake",
            model="fake-1",
            usage={"input_tokens": 10, "output_tokens": 5},
        )
    )
    second = await commentary.insert(
        CommentaryDraft(
            fixture_id=fixture.id,
            commentator_id=voice.id,
            text="And the reply.",
            triggering_event_id=stored.id,
            in_reply_to=first.id,
            provider="fake",
            model="fake-1",
            usage=None,
        )
    )

    assert await commentary.last_triggering_event_id(fixture.id) == stored.id
    listed = await commentary.list_for_fixture(fixture.id)
    assert [m.id for m in listed] == [first.id, second.id]
    assert listed[0].usage == {"input_tokens": 10, "output_tokens": 5}
    assert listed[1].in_reply_to == first.id

    tail = await commentary.list_after(fixture_id=fixture.id, after_message_id=first.id)
    assert [m.id for m in tail] == [second.id]


async def test_reference_upserts_create_then_update(sessions: SessionFactory) -> None:
    reference = SqlReferenceRepository(sessions)

    league = League(
        api_league_id=9990,
        name="Demo Premier League",
        type="League",
        country=Country(name="Demoland", code="DM"),
    )
    league_id = await reference.upsert_league(league)
    assert await reference.upsert_league(league.model_copy(update={"name": "DPL"})) == league_id

    season = Season(year=2025, current=True, coverage=COVERAGE)
    season_id = await reference.upsert_season(league_id=league_id, season=season)
    assert (
        await reference.upsert_season(
            league_id=league_id, season=season.model_copy(update={"current": False})
        )
        == season_id
    )

    venue = Venue(api_venue_id=77, name="Riverton Park", city="Riverton")
    venue_id = await reference.upsert_venue(venue)
    assert await reference.upsert_venue(venue.model_copy(update={"city": "New City"})) == venue_id

    team = TeamProfile(
        api_team_id=501,
        name="Riverton Albion",
        code="RIV",
        country="Demoland",
        founded=1901,
        logo="h.png",
    )
    team_id = await reference.upsert_team(team=team, venue_id=venue_id)
    assert (
        await reference.upsert_team(
            team=team.model_copy(update={"name": "Riverton Albion FC"}), venue_id=venue_id
        )
        == team_id
    )

    async with sessions() as session:
        row = await session.get(TeamRow, team_id)
        assert row is not None
        assert row.name == "Riverton Albion FC"
        assert row.venue_id == venue_id


async def test_minimal_league_enriched_by_reference_sync(sessions: SessionFactory) -> None:
    fixtures = SqlFixtureRepository(sessions)
    reference = SqlReferenceRepository(sessions)

    fixture = await fixtures.upsert_snapshot(make_snapshot())
    league_id = await reference.upsert_league(
        League(
            api_league_id=fixture.league.api_league_id,
            name="Demo Premier League",
            type="League",
            country=Country(name="Demoland", code="DM"),
        )
    )
    refreshed = await fixtures.get(fixture.id)
    assert refreshed.league.api_league_id == fixture.league.api_league_id
    assert league_id > 0


async def test_request_log_records(sessions: SessionFactory) -> None:
    log = SqlApiRequestLogRepository(sessions)
    await log.record(endpoint="fixtures", requests_remaining=41)
    await log.record(endpoint="fixtures", requests_remaining=17)
    async with sessions() as session:
        rows = (await session.scalars(select(ApiRequestLogRow))).all()
        assert [(r.endpoint, r.requests_remaining) for r in rows] == [
            ("fixtures", 41),
            ("fixtures", 17),
        ]


async def test_event_team_upsert_reuses_fixture_team(sessions: SessionFactory) -> None:
    fixtures = SqlFixtureRepository(sessions)
    events = SqlEventRepository(sessions)
    fixture = await fixtures.upsert_snapshot(make_snapshot())
    await events.insert_if_new(fixture_id=fixture.id, event=make_event(team=AWAY))
    async with sessions() as session:
        teams = (await session.scalars(select(TeamRow))).all()
        assert len(teams) == 2  # home + away, no duplicate row for the event team
