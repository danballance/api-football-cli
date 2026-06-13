"""Repository ports (architecture §5). The application owns these interfaces."""

from __future__ import annotations

from typing import Protocol

from api_football_cli.domain.entities import (
    CommentaryDraft,
    CommentaryMessage,
    Commentator,
    Fixture,
    FixtureSnapshot,
    League,
    ObservedEvent,
    Season,
    StoredFixtureEvent,
    TeamProfile,
    Venue,
)
from api_football_cli.domain.personas import PersonaSeed


class NotFoundError(RuntimeError):
    """Raised when a required row does not exist."""


class FixtureRepository(Protocol):
    async def upsert_snapshot(self, snapshot: FixtureSnapshot) -> Fixture:
        """Upsert the fixture (and minimal league/season/team rows) from a poll."""
        ...

    async def get(self, fixture_id: int) -> Fixture: ...

    async def list_all(self) -> list[Fixture]: ...


class EventRepository(Protocol):
    async def insert_if_new(
        self, *, fixture_id: int, event: ObservedEvent
    ) -> StoredFixtureEvent | None:
        """Append the event unless its content hash is already stored (append-only)."""
        ...

    async def list_for_fixture(self, fixture_id: int) -> list[StoredFixtureEvent]: ...

    async def list_after(
        self, *, fixture_id: int, after_event_id: int
    ) -> list[StoredFixtureEvent]: ...


class CommentatorRepository(Protocol):
    async def upsert(self, seed: PersonaSeed) -> Commentator:
        """Upsert by unique name; returns the stored commentator."""
        ...

    async def list_all(self) -> list[Commentator]: ...


class CommentaryRepository(Protocol):
    async def insert(self, draft: CommentaryDraft) -> CommentaryMessage: ...

    async def list_for_fixture(self, fixture_id: int) -> list[CommentaryMessage]: ...

    async def list_after(
        self, *, fixture_id: int, after_message_id: int
    ) -> list[CommentaryMessage]: ...

    async def last_triggering_event_id(self, fixture_id: int) -> int:
        """Highest event id already commented on for the fixture; 0 when none."""
        ...


class ReferenceRepository(Protocol):
    """Upserts for reference-data sync, keyed on api_*_id columns."""

    async def upsert_league(self, league: League) -> int: ...

    async def upsert_season(self, *, league_id: int, season: Season) -> int: ...

    async def upsert_venue(self, venue: Venue) -> int: ...

    async def upsert_team(self, *, team: TeamProfile, venue_id: int | None) -> int: ...


class ApiRequestLogRepository(Protocol):
    async def record(self, *, endpoint: str, requests_remaining: int | None) -> None: ...
