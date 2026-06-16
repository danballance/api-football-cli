"""SQLAlchemy 2.0 typed table definitions (architecture §5).

Every table keys on our own surrogate ``id``; api-football ids are stored as
``api_*_id`` reference columns only and never used as our keys.
``fixture_event`` is append-only: rows are inserted and never updated.

Types carry SQLite variants so the suite runs against aiosqlite while
production runs on Postgres (JSONB, BIGINT identity).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Our surrogate primary key: BIGINT on Postgres, plain INTEGER on SQLite so
# autoincrement works there too.
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")
JsonValue = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CountryRow(Base):
    __tablename__ = "country"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    code: Mapped[str | None] = mapped_column(String(8))


class LeagueRow(Base):
    __tablename__ = "league"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    api_league_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(200))
    # Nullable: live ingestion creates minimal league rows; `afc sync leagues`
    # fills the type in.
    type: Mapped[str | None] = mapped_column(String(40))
    country_id: Mapped[int | None] = mapped_column(ForeignKey("country.id"))


class SeasonRow(Base):
    __tablename__ = "season"
    __table_args__ = (UniqueConstraint("league_id", "year"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("league.id"))
    year: Mapped[int] = mapped_column(Integer)
    current: Mapped[bool] = mapped_column(Boolean)
    coverage: Mapped[dict[str, bool] | None] = mapped_column(JsonValue)


class VenueRow(Base):
    __tablename__ = "venue"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    api_venue_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(120))


class TeamRow(Base):
    __tablename__ = "team"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    api_team_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(8))
    country: Mapped[str | None] = mapped_column(String(120))
    founded: Mapped[int | None] = mapped_column(Integer)
    logo: Mapped[str | None] = mapped_column(String(400))
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venue.id"))


class PlayerRow(Base):
    __tablename__ = "player"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    api_player_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str | None] = mapped_column(String(200))


class FixtureRow(Base):
    __tablename__ = "fixture"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    api_fixture_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("league.id"))
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id"))
    kickoff_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status_short: Mapped[str] = mapped_column(String(8))
    elapsed: Mapped[int | None] = mapped_column(Integer)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    home_goals: Mapped[int | None] = mapped_column(Integer)
    away_goals: Mapped[int | None] = mapped_column(Integer)
    referee: Mapped[str | None] = mapped_column(String(200))


class FixtureEventRow(Base):
    """Append-only: no updated_at, rows are never mutated (architecture §2)."""

    __tablename__ = "fixture_event"
    __table_args__ = (UniqueConstraint("fixture_id", "event_hash"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixture.id"))
    event_hash: Mapped[str] = mapped_column(String(40))
    elapsed: Mapped[int] = mapped_column(Integer)
    extra: Mapped[int | None] = mapped_column(Integer)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("player.id"))
    assist_id: Mapped[int | None] = mapped_column(ForeignKey("player.id"))
    type: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(String(200))
    comments: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CommentatorRow(Base):
    __tablename__ = "commentator"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    role: Mapped[str] = mapped_column(String(20))
    system_prompt: Mapped[str] = mapped_column(Text)
    style: Mapped[dict[str, object]] = mapped_column(JsonValue)


class CommentaryMessageRow(Base):
    __tablename__ = "commentary_message"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixture.id"))
    commentator_id: Mapped[int] = mapped_column(ForeignKey("commentator.id"))
    text: Mapped[str] = mapped_column(Text)
    triggering_event_id: Mapped[int | None] = mapped_column(ForeignKey("fixture_event.id"))
    in_reply_to: Mapped[int | None] = mapped_column(ForeignKey("commentary_message.id"))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    usage: Mapped[dict[str, int] | None] = mapped_column(JsonValue)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApiRequestLogRow(Base):
    __tablename__ = "api_request_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(120))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requests_remaining: Mapped[int] = mapped_column(Integer)
