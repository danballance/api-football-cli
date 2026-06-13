"""In-memory port implementations for service tests.

The in-memory event and commentary repositories publish a notification on
insert when given a bus — mirroring the Postgres AFTER INSERT triggers that
do this in production (architecture §5).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import count

from api_football_cli.application.ports.event_bus import (
    COMMENTARY_CHANNEL,
    FIXTURE_EVENTS_CHANNEL,
    EventBus,
    Notification,
)
from api_football_cli.application.ports.football_api import ApiFootballError
from api_football_cli.application.ports.repositories import NotFoundError
from api_football_cli.domain.entities import (
    AccountStatus,
    CommentaryDraft,
    CommentaryMessage,
    CommentaryResult,
    Commentator,
    Fixture,
    FixtureSnapshot,
    League,
    LeagueWithSeasons,
    ObservedEvent,
    Season,
    SpeakerRole,
    StoredFixtureEvent,
    StoredTeam,
    TeamProfile,
    TeamWithVenue,
    Turn,
    Venue,
)
from api_football_cli.domain.events import event_hash
from api_football_cli.domain.personas import PersonaSeed

_TICKS = count()
_EPOCH = datetime(2025, 8, 16, 14, 0, tzinfo=UTC)


def next_timestamp() -> datetime:
    """Strictly increasing timestamps shared by all fakes."""
    return _EPOCH + timedelta(milliseconds=next(_TICKS))


class InMemoryFixtureRepository:
    def __init__(self) -> None:
        self._by_id: dict[int, Fixture] = {}
        self._ids = count(1)

    async def upsert_snapshot(self, snapshot: FixtureSnapshot) -> Fixture:
        existing = next(
            (f for f in self._by_id.values() if f.api_fixture_id == snapshot.api_fixture_id),
            None,
        )
        fixture_id = existing.id if existing else next(self._ids)
        fixture = Fixture(
            id=fixture_id,
            api_fixture_id=snapshot.api_fixture_id,
            league=snapshot.league,
            kickoff=snapshot.kickoff,
            status=snapshot.status,
            elapsed=snapshot.elapsed,
            home=StoredTeam(
                id=snapshot.home.api_team_id,
                api_team_id=snapshot.home.api_team_id,
                name=snapshot.home.name,
            ),
            away=StoredTeam(
                id=snapshot.away.api_team_id,
                api_team_id=snapshot.away.api_team_id,
                name=snapshot.away.name,
            ),
            home_goals=snapshot.home_goals,
            away_goals=snapshot.away_goals,
            referee=snapshot.referee,
        )
        self._by_id[fixture_id] = fixture
        return fixture

    async def get(self, fixture_id: int) -> Fixture:
        if fixture_id not in self._by_id:
            raise NotFoundError(f"fixture {fixture_id} not found")
        return self._by_id[fixture_id]

    async def list_all(self) -> list[Fixture]:
        return [self._by_id[key] for key in sorted(self._by_id)]


class InMemoryEventRepository:
    def __init__(self, *, bus: EventBus | None) -> None:
        self._bus = bus
        self.stored: list[StoredFixtureEvent] = []
        self._hashes: set[tuple[int, str]] = set()
        self._ids = count(1)

    async def insert_if_new(
        self, *, fixture_id: int, event: ObservedEvent
    ) -> StoredFixtureEvent | None:
        digest = event_hash(event)
        if (fixture_id, digest) in self._hashes:
            return None
        self._hashes.add((fixture_id, digest))
        stored = StoredFixtureEvent(
            id=next(self._ids),
            fixture_id=fixture_id,
            event_hash=digest,
            event=event,
            created_at=next_timestamp(),
        )
        self.stored.append(stored)
        if self._bus is not None:
            # Mirror the AFTER INSERT trigger.
            await self._bus.publish(
                FIXTURE_EVENTS_CHANNEL, Notification(fixture_id=fixture_id, id=stored.id)
            )
        return stored

    async def list_for_fixture(self, fixture_id: int) -> list[StoredFixtureEvent]:
        return [event for event in self.stored if event.fixture_id == fixture_id]

    async def list_after(
        self, *, fixture_id: int, after_event_id: int
    ) -> list[StoredFixtureEvent]:
        return [
            event
            for event in self.stored
            if event.fixture_id == fixture_id and event.id > after_event_id
        ]


class InMemoryCommentaryRepository:
    def __init__(self, *, bus: EventBus | None) -> None:
        self._bus = bus
        self.messages: list[CommentaryMessage] = []
        self._ids = count(1)

    async def insert(self, draft: CommentaryDraft) -> CommentaryMessage:
        message = CommentaryMessage(
            id=next(self._ids),
            fixture_id=draft.fixture_id,
            commentator_id=draft.commentator_id,
            text=draft.text,
            triggering_event_id=draft.triggering_event_id,
            in_reply_to=draft.in_reply_to,
            provider=draft.provider,
            model=draft.model,
            usage=draft.usage,
            created_at=next_timestamp(),
        )
        self.messages.append(message)
        if self._bus is not None:
            await self._bus.publish(
                COMMENTARY_CHANNEL, Notification(fixture_id=message.fixture_id, id=message.id)
            )
        return message

    async def list_for_fixture(self, fixture_id: int) -> list[CommentaryMessage]:
        return [m for m in self.messages if m.fixture_id == fixture_id]

    async def list_after(
        self, *, fixture_id: int, after_message_id: int
    ) -> list[CommentaryMessage]:
        return [
            m
            for m in self.messages
            if m.fixture_id == fixture_id and m.id > after_message_id
        ]

    async def last_triggering_event_id(self, fixture_id: int) -> int:
        triggering = [
            m.triggering_event_id
            for m in self.messages
            if m.fixture_id == fixture_id and m.triggering_event_id is not None
        ]
        return max(triggering, default=0)


class InMemoryCommentatorRepository:
    def __init__(self) -> None:
        self._by_name: dict[str, Commentator] = {}
        self._ids = count(1)

    async def upsert(self, seed: PersonaSeed) -> Commentator:
        existing = self._by_name.get(seed.name)
        commentator = Commentator(
            id=existing.id if existing else next(self._ids),
            name=seed.name,
            role=seed.role,
            system_prompt=seed.system_prompt,
            style=seed.style,
        )
        self._by_name[seed.name] = commentator
        return commentator

    async def list_all(self) -> list[Commentator]:
        return sorted(self._by_name.values(), key=lambda c: c.id)


class InMemoryReferenceRepository:
    def __init__(self) -> None:
        self.leagues: dict[int, League] = {}
        self.seasons: dict[tuple[int, int], Season] = {}
        self.venues: dict[int, Venue] = {}
        self.teams: dict[int, TeamProfile] = {}
        self._ids = count(1)

    async def upsert_league(self, league: League) -> int:
        self.leagues[league.api_league_id] = league
        return league.api_league_id

    async def upsert_season(self, *, league_id: int, season: Season) -> int:
        self.seasons[(league_id, season.year)] = season
        return next(self._ids)

    async def upsert_venue(self, venue: Venue) -> int:
        self.venues[venue.api_venue_id] = venue
        return venue.api_venue_id

    async def upsert_team(self, *, team: TeamProfile, venue_id: int | None) -> int:
        self.teams[team.api_team_id] = team
        return team.api_team_id


class InMemoryRequestLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, int | None]] = []

    async def record(self, *, endpoint: str, requests_remaining: int | None) -> None:
        self.records.append((endpoint, requests_remaining))


class StubFootballApi:
    """Scriptable FootballApi: returns queued snapshots/event batches in order."""

    def __init__(
        self,
        *,
        snapshots: list[FixtureSnapshot],
        event_batches: list[list[ObservedEvent]],
        remaining: int | None,
        leagues: list[LeagueWithSeasons] | None = None,
        teams: list[TeamWithVenue] | None = None,
        league_fixtures: list[FixtureSnapshot] | None = None,
        status: AccountStatus | None = None,
    ) -> None:
        self._snapshots = list(snapshots)
        self._event_batches = list(event_batches)
        self._remaining = remaining
        self._leagues = leagues or []
        self._teams = teams or []
        self._league_fixtures = league_fixtures or []
        self._status = status

    async def fixture(self, api_fixture_id: int) -> FixtureSnapshot:
        if not self._snapshots:
            raise ApiFootballError("stub has no snapshots left")
        if len(self._snapshots) == 1:
            return self._snapshots[0]
        return self._snapshots.pop(0)

    async def fixtures_events(self, api_fixture_id: int) -> list[ObservedEvent]:
        if not self._event_batches:
            return []
        if len(self._event_batches) == 1:
            return list(self._event_batches[0])
        return list(self._event_batches.pop(0))

    async def leagues(self, *, season: int) -> list[LeagueWithSeasons]:
        return list(self._leagues)

    async def teams(self, *, league_api_id: int, season: int) -> list[TeamWithVenue]:
        return list(self._teams)

    async def fixtures_by_league(
        self, *, league_api_id: int, season: int
    ) -> list[FixtureSnapshot]:
        return list(self._league_fixtures)

    async def account_status(self) -> AccountStatus:
        if self._status is None:
            raise ApiFootballError("stub has no account status")
        return self._status

    def requests_remaining(self) -> int | None:
        return self._remaining


class RecordingModel:
    """CommentaryModel that returns numbered lines and records its inputs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Turn, ...]]] = []
        self._counter = count(1)

    @property
    def provider(self) -> str:
        return "recording"

    @property
    def model_id(self) -> str:
        return "recorder-1"

    async def generate(self, *, system: str, transcript: Sequence[Turn]) -> CommentaryResult:
        self.calls.append((system, tuple(transcript)))
        return CommentaryResult(
            text=f"line-{next(self._counter)}", usage={"input_tokens": 1, "output_tokens": 1}
        )


def feed_turns(transcript: tuple[Turn, ...]) -> list[str]:
    return [t.text for t in transcript if t.speaker is SpeakerRole.MATCH_FEED]
