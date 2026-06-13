"""Domain entities and value objects. Pure data, no I/O, no framework."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class FixtureStatus(StrEnum):
    """api-football fixture status shorts."""

    TBD = "TBD"
    NOT_STARTED = "NS"
    FIRST_HALF = "1H"
    HALF_TIME = "HT"
    SECOND_HALF = "2H"
    EXTRA_TIME = "ET"
    BREAK_TIME = "BT"
    PENALTY_SHOOTOUT = "P"
    SUSPENDED = "SUSP"
    INTERRUPTED = "INT"
    FULL_TIME = "FT"
    AFTER_EXTRA_TIME = "AET"
    AFTER_PENALTIES = "PEN"
    POSTPONED = "PST"
    CANCELLED = "CANC"
    ABANDONED = "ABD"
    TECHNICAL_LOSS = "AWD"
    WALKOVER = "WO"
    LIVE = "LIVE"


# Statuses after which a fixture will not produce further events. The
# architecture doc names {FT, AET, PEN}; the administrative endings are
# included because polling them would never terminate either.
TERMINAL_STATUSES: frozenset[FixtureStatus] = frozenset(
    {
        FixtureStatus.FULL_TIME,
        FixtureStatus.AFTER_EXTRA_TIME,
        FixtureStatus.AFTER_PENALTIES,
        FixtureStatus.POSTPONED,
        FixtureStatus.CANCELLED,
        FixtureStatus.ABANDONED,
        FixtureStatus.TECHNICAL_LOSS,
        FixtureStatus.WALKOVER,
    }
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class TeamRef(FrozenModel):
    """A team as referenced by the upstream feed."""

    api_team_id: int
    name: str


class PlayerRef(FrozenModel):
    """A player as referenced by the upstream feed (sometimes anonymous)."""

    api_player_id: int | None
    name: str | None


class LeagueRef(FrozenModel):
    """League context attached to a fixture snapshot."""

    api_league_id: int
    name: str
    country: str | None
    season: int


class FixtureSnapshot(FrozenModel):
    """The state of a fixture as reported by the feed at one poll."""

    api_fixture_id: int
    league: LeagueRef
    kickoff: datetime
    status: FixtureStatus
    elapsed: int | None
    home: TeamRef
    away: TeamRef
    home_goals: int | None
    away_goals: int | None
    referee: str | None


class StoredTeam(FrozenModel):
    """A team row in our database (our id + upstream reference)."""

    id: int
    api_team_id: int
    name: str


class Fixture(FrozenModel):
    """A fixture row in our database."""

    id: int
    api_fixture_id: int
    league: LeagueRef
    kickoff: datetime
    status: FixtureStatus
    elapsed: int | None
    home: StoredTeam
    away: StoredTeam
    home_goals: int | None
    away_goals: int | None
    referee: str | None


class ObservedEvent(FrozenModel):
    """One event as reported by /fixtures/events. Identity = content hash."""

    elapsed: int
    extra: int | None
    team: TeamRef
    player: PlayerRef | None
    assist: PlayerRef | None
    type: str
    detail: str
    comments: str | None


class StoredFixtureEvent(FrozenModel):
    """An appended row of our append-only event log."""

    id: int
    fixture_id: int
    event_hash: str
    event: ObservedEvent
    created_at: datetime


class CommentatorRole(StrEnum):
    PLAY_BY_PLAY = "play_by_play"
    COLOR = "color"


class CommentatorStyle(FrozenModel):
    tone: str
    quirks: tuple[str, ...]


class Commentator(FrozenModel):
    id: int
    name: str
    role: CommentatorRole
    system_prompt: str
    style: CommentatorStyle


class CommentaryDraft(FrozenModel):
    """A commentary line ready to be persisted."""

    fixture_id: int
    commentator_id: int
    text: str
    triggering_event_id: int | None
    in_reply_to: int | None
    provider: str
    model: str
    usage: dict[str, int] | None


class CommentaryMessage(FrozenModel):
    """A persisted commentary line."""

    id: int
    fixture_id: int
    commentator_id: int
    text: str
    triggering_event_id: int | None
    in_reply_to: int | None
    provider: str
    model: str
    usage: dict[str, int] | None
    created_at: datetime


class SpeakerRole(StrEnum):
    """Provider-neutral tags for booth-transcript turns (architecture §8)."""

    THIS_COMMENTATOR = "this_commentator"
    CO_COMMENTATOR = "co_commentator"
    MATCH_FEED = "match_feed"


class Turn(FrozenModel):
    """One line in the booth transcript, tagged for one commentator's view."""

    speaker: SpeakerRole
    text: str


class CommentaryResult(FrozenModel):
    """What a commentary model returns: a complete spoken line."""

    text: str
    usage: dict[str, int] | None


# --- Reference data (sync commands) -----------------------------------------


class Country(FrozenModel):
    name: str
    code: str | None


class League(FrozenModel):
    api_league_id: int
    name: str
    type: str
    country: Country


class Coverage(FrozenModel):
    events: bool
    lineups: bool
    statistics_fixtures: bool
    statistics_players: bool
    standings: bool
    players: bool
    top_scorers: bool
    top_assists: bool
    top_cards: bool
    injuries: bool
    predictions: bool
    odds: bool


class Season(FrozenModel):
    year: int
    current: bool
    coverage: Coverage


class LeagueWithSeasons(FrozenModel):
    league: League
    seasons: tuple[Season, ...]


class Venue(FrozenModel):
    api_venue_id: int
    name: str
    city: str | None


class TeamProfile(FrozenModel):
    api_team_id: int
    name: str
    code: str | None
    country: str | None
    founded: int | None
    logo: str | None


class TeamWithVenue(FrozenModel):
    team: TeamProfile
    venue: Venue | None


class AccountStatus(FrozenModel):
    """Subset of GET /status we care about (does not consume quota)."""

    account_name: str
    plan: str
    active: bool
    requests_today: int
    daily_limit: int
