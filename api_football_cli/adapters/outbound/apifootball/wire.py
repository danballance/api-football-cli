"""Wire models for api-football v3 responses, plus mapping to domain.

These mirror the JSON shapes in .tasks/api-football.schema.yaml. Unknown
fields are ignored; fields we rely on are validated strictly so a surprising
payload fails fast instead of flowing onward half-parsed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from api_football_cli.domain.entities import (
    AccountStatus,
    Country,
    Coverage,
    FixtureSnapshot,
    FixtureStatus,
    League,
    LeagueRef,
    LeagueWithSeasons,
    ObservedEvent,
    PlayerRef,
    Season,
    TeamProfile,
    TeamRef,
    TeamWithVenue,
    Venue,
)


class WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --- fixtures ----------------------------------------------------------------


class WireFixtureStatus(WireModel):
    long: str
    short: str
    elapsed: int | None
    extra: int | None = None


class WireFixtureInner(WireModel):
    id: int
    referee: str | None
    date: datetime
    status: WireFixtureStatus


class WireLeagueFixture(WireModel):
    id: int
    name: str
    country: str | None
    season: int


class WireTeamSide(WireModel):
    id: int
    name: str


class WireTeams(WireModel):
    home: WireTeamSide
    away: WireTeamSide


class WireGoals(WireModel):
    home: int | None
    away: int | None


class WireFixtureItem(WireModel):
    fixture: WireFixtureInner
    league: WireLeagueFixture
    teams: WireTeams
    goals: WireGoals

    def to_domain(self) -> FixtureSnapshot:
        return FixtureSnapshot(
            api_fixture_id=self.fixture.id,
            league=LeagueRef(
                api_league_id=self.league.id,
                name=self.league.name,
                country=self.league.country,
                season=self.league.season,
            ),
            kickoff=self.fixture.date,
            status=FixtureStatus(self.fixture.status.short),
            elapsed=self.fixture.status.elapsed,
            home=TeamRef(api_team_id=self.teams.home.id, name=self.teams.home.name),
            away=TeamRef(api_team_id=self.teams.away.id, name=self.teams.away.name),
            home_goals=self.goals.home,
            away_goals=self.goals.away,
            referee=self.fixture.referee,
        )


# --- fixture events ----------------------------------------------------------


class WireEventTime(WireModel):
    elapsed: int
    extra: int | None


class WireNamedId(WireModel):
    id: int | None
    name: str | None

    def to_player(self) -> PlayerRef | None:
        if self.id is None and self.name is None:
            return None
        return PlayerRef(api_player_id=self.id, name=self.name)


class WireEventTeam(WireModel):
    id: int
    name: str


class WireEventItem(WireModel):
    time: WireEventTime
    team: WireEventTeam
    player: WireNamedId
    assist: WireNamedId
    type: str
    detail: str
    comments: str | None

    def to_domain(self) -> ObservedEvent:
        return ObservedEvent(
            elapsed=self.time.elapsed,
            extra=self.time.extra,
            team=TeamRef(api_team_id=self.team.id, name=self.team.name),
            player=self.player.to_player(),
            assist=self.assist.to_player(),
            type=self.type,
            detail=self.detail,
            comments=self.comments,
        )


# --- leagues -----------------------------------------------------------------


class WireCountry(WireModel):
    name: str
    code: str | None

    def to_domain(self) -> Country:
        return Country(name=self.name, code=self.code)


class WireLeague(WireModel):
    id: int
    name: str
    type: str


class WireCoverageFixtures(WireModel):
    events: bool
    lineups: bool
    statistics_fixtures: bool
    statistics_players: bool


class WireCoverage(WireModel):
    fixtures: WireCoverageFixtures
    standings: bool
    players: bool
    top_scorers: bool
    top_assists: bool
    top_cards: bool
    injuries: bool
    predictions: bool
    odds: bool

    def to_domain(self) -> Coverage:
        return Coverage(
            events=self.fixtures.events,
            lineups=self.fixtures.lineups,
            statistics_fixtures=self.fixtures.statistics_fixtures,
            statistics_players=self.fixtures.statistics_players,
            standings=self.standings,
            players=self.players,
            top_scorers=self.top_scorers,
            top_assists=self.top_assists,
            top_cards=self.top_cards,
            injuries=self.injuries,
            predictions=self.predictions,
            odds=self.odds,
        )


class WireSeason(WireModel):
    year: int
    current: bool
    coverage: WireCoverage

    def to_domain(self) -> Season:
        return Season(year=self.year, current=self.current, coverage=self.coverage.to_domain())


class WireLeagueItem(WireModel):
    league: WireLeague
    country: WireCountry
    seasons: list[WireSeason]

    def to_domain(self) -> LeagueWithSeasons:
        return LeagueWithSeasons(
            league=League(
                api_league_id=self.league.id,
                name=self.league.name,
                type=self.league.type,
                country=self.country.to_domain(),
            ),
            seasons=tuple(season.to_domain() for season in self.seasons),
        )


# --- teams -------------------------------------------------------------------


class WireTeamInfo(WireModel):
    id: int
    name: str
    code: str | None
    country: str | None
    founded: int | None
    logo: str | None


class WireVenueFull(WireModel):
    id: int | None
    name: str | None
    city: str | None


class WireTeamItem(WireModel):
    team: WireTeamInfo
    venue: WireVenueFull | None

    def to_domain(self) -> TeamWithVenue:
        venue: Venue | None = None
        if self.venue is not None and self.venue.id is not None and self.venue.name is not None:
            venue = Venue(api_venue_id=self.venue.id, name=self.venue.name, city=self.venue.city)
        return TeamWithVenue(
            team=TeamProfile(
                api_team_id=self.team.id,
                name=self.team.name,
                code=self.team.code,
                country=self.team.country,
                founded=self.team.founded,
                logo=self.team.logo,
            ),
            venue=venue,
        )


# --- status ------------------------------------------------------------------


class WireAccount(WireModel):
    firstname: str
    lastname: str


class WireSubscription(WireModel):
    plan: str
    active: bool


class WireRequests(WireModel):
    current: int
    limit_day: int


class WireStatusItem(WireModel):
    account: WireAccount
    subscription: WireSubscription
    requests: WireRequests

    def to_domain(self) -> AccountStatus:
        return AccountStatus(
            account_name=f"{self.account.firstname} {self.account.lastname}",
            plan=self.subscription.plan,
            active=self.subscription.active,
            requests_today=self.requests.current,
            daily_limit=self.requests.limit_day,
        )
