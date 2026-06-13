"""SyncReferenceData: crawls upsert reference rows and report counts."""

from __future__ import annotations

from api_football_cli.application.services.sync_reference import SyncReferenceData
from api_football_cli.domain.entities import (
    Country,
    Coverage,
    FixtureStatus,
    League,
    LeagueWithSeasons,
    Season,
    TeamProfile,
    TeamWithVenue,
    Venue,
)

from tests.factories import make_snapshot
from tests.fakes import (
    InMemoryFixtureRepository,
    InMemoryReferenceRepository,
    StubFootballApi,
)

COVERAGE = Coverage(
    events=True,
    lineups=True,
    statistics_fixtures=False,
    statistics_players=False,
    standings=True,
    players=True,
    top_scorers=True,
    top_assists=True,
    top_cards=True,
    injuries=False,
    predictions=True,
    odds=False,
)

LEAGUES = [
    LeagueWithSeasons(
        league=League(
            api_league_id=9990,
            name="Demo Premier League",
            type="League",
            country=Country(name="Demoland", code="DM"),
        ),
        seasons=(
            Season(year=2024, current=False, coverage=COVERAGE),
            Season(year=2025, current=True, coverage=COVERAGE),
        ),
    )
]

TEAMS = [
    TeamWithVenue(
        team=TeamProfile(
            api_team_id=501,
            name="Riverton Albion",
            code="RIV",
            country="Demoland",
            founded=1901,
            logo="h.png",
        ),
        venue=Venue(api_venue_id=77, name="Riverton Park", city="Riverton"),
    ),
    TeamWithVenue(
        team=TeamProfile(
            api_team_id=502,
            name="Kingsport Wanderers",
            code="KSW",
            country="Demoland",
            founded=1905,
            logo="a.png",
        ),
        venue=None,
    ),
]


def build_service(
    api: StubFootballApi,
) -> tuple[SyncReferenceData, InMemoryReferenceRepository, InMemoryFixtureRepository]:
    reference = InMemoryReferenceRepository()
    fixtures = InMemoryFixtureRepository()
    return (
        SyncReferenceData(api=api, reference=reference, fixtures=fixtures),
        reference,
        fixtures,
    )


async def test_sync_leagues() -> None:
    api = StubFootballApi(snapshots=[], event_batches=[], remaining=None, leagues=LEAGUES)
    service, reference, _ = build_service(api)
    report = await service.sync_leagues(season=2025)
    assert (report.leagues, report.seasons) == (1, 2)
    assert reference.leagues[9990].name == "Demo Premier League"
    assert (9990, 2025) in reference.seasons


async def test_sync_teams_counts_optional_venues() -> None:
    api = StubFootballApi(snapshots=[], event_batches=[], remaining=None, teams=TEAMS)
    service, reference, _ = build_service(api)
    report = await service.sync_teams(league_api_id=9990, season=2025)
    assert (report.teams, report.venues) == (2, 1)
    assert reference.teams[502].name == "Kingsport Wanderers"
    assert 77 in reference.venues


async def test_sync_fixtures() -> None:
    api = StubFootballApi(
        snapshots=[],
        event_batches=[],
        remaining=None,
        league_fixtures=[
            make_snapshot(api_fixture_id=1001),
            make_snapshot(api_fixture_id=1002, status=FixtureStatus.FULL_TIME),
        ],
    )
    service, _, fixtures = build_service(api)
    report = await service.sync_fixtures(league_api_id=9990, season=2025)
    assert report.fixtures == 2
    assert len(await fixtures.list_all()) == 2
