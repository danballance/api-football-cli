"""Edge DTOs for the web adapter. Domain entities never leak to the wire."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from api_football_cli.domain.entities import (
    CommentaryMessage,
    Commentator,
    Fixture,
    StoredFixtureEvent,
)
from api_football_cli.domain.events import minute_label, render_event


class EdgeModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class TeamDTO(EdgeModel):
    id: int
    name: str


class FixtureDTO(EdgeModel):
    id: int
    api_fixture_id: int
    league: str
    season: int
    kickoff: datetime
    status: str
    elapsed: int | None
    home: TeamDTO
    away: TeamDTO
    home_goals: int | None
    away_goals: int | None

    @classmethod
    def from_domain(cls, fixture: Fixture) -> FixtureDTO:
        return cls(
            id=fixture.id,
            api_fixture_id=fixture.api_fixture_id,
            league=fixture.league.name,
            season=fixture.league.season,
            kickoff=fixture.kickoff,
            status=fixture.status.value,
            elapsed=fixture.elapsed,
            home=TeamDTO(id=fixture.home.id, name=fixture.home.name),
            away=TeamDTO(id=fixture.away.id, name=fixture.away.name),
            home_goals=fixture.home_goals,
            away_goals=fixture.away_goals,
        )


class EventDTO(EdgeModel):
    id: int
    minute: str
    type: str
    detail: str
    team: str
    text: str

    @classmethod
    def from_domain(cls, stored: StoredFixtureEvent) -> EventDTO:
        return cls(
            id=stored.id,
            minute=minute_label(stored.event),
            type=stored.event.type,
            detail=stored.event.detail,
            team=stored.event.team.name,
            text=render_event(stored.event),
        )


class CommentatorDTO(EdgeModel):
    id: int
    name: str
    role: str

    @classmethod
    def from_domain(cls, commentator: Commentator) -> CommentatorDTO:
        return cls(id=commentator.id, name=commentator.name, role=commentator.role.value)


class CommentaryDTO(EdgeModel):
    id: int
    fixture_id: int
    commentator_id: int
    text: str
    in_reply_to: int | None
    created_at: datetime

    @classmethod
    def from_domain(cls, message: CommentaryMessage) -> CommentaryDTO:
        return cls(
            id=message.id,
            fixture_id=message.fixture_id,
            commentator_id=message.commentator_id,
            text=message.text,
            in_reply_to=message.in_reply_to,
            created_at=message.created_at,
        )
