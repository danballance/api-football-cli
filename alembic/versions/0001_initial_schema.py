"""Initial schema + LISTEN/NOTIFY triggers.

Revision ID: 0001
Revises:
Create Date: 2026-06-13

Mirrors api_football_cli.adapters.outbound.persistence.tables. On Postgres the
ids are GENERATED ALWAYS AS IDENTITY and AFTER INSERT triggers publish tiny
{fixture_id, id} payloads on fixture_event_inserted / commentary_inserted —
the reactive spine of the runtime (architecture §5). On SQLite (used only by
the test suite) identity and triggers are skipped.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_BIG_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_JSON = sa.JSON().with_variant(JSONB(), "postgresql")

# asyncpg refuses multiple commands per statement: one op.execute() per command.
_TRIGGER_STATEMENTS = (
    """
CREATE OR REPLACE FUNCTION notify_fixture_event() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify(
    'fixture_event_inserted',
    json_build_object('fixture_id', NEW.fixture_id, 'id', NEW.id)::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER fixture_event_notify
AFTER INSERT ON fixture_event
FOR EACH ROW EXECUTE FUNCTION notify_fixture_event()
""",
    """
CREATE OR REPLACE FUNCTION notify_commentary() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify(
    'commentary_inserted',
    json_build_object('fixture_id', NEW.fixture_id, 'id', NEW.id)::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER commentary_notify
AFTER INSERT ON commentary_message
FOR EACH ROW EXECUTE FUNCTION notify_commentary()
""",
)

_DROP_TRIGGER_STATEMENTS = (
    "DROP TRIGGER IF EXISTS commentary_notify ON commentary_message",
    "DROP FUNCTION IF EXISTS notify_commentary()",
    "DROP TRIGGER IF EXISTS fixture_event_notify ON fixture_event",
    "DROP FUNCTION IF EXISTS notify_fixture_event()",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _id_column() -> sa.Column:
    if _is_postgres():
        return sa.Column("id", _BIG_PK, sa.Identity(always=True), primary_key=True)
    return sa.Column("id", _BIG_PK, primary_key=True)


def upgrade() -> None:
    op.create_table(
        "country",
        _id_column(),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("code", sa.String(8), nullable=True),
    )
    op.create_table(
        "league",
        _id_column(),
        sa.Column("api_league_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("type", sa.String(40), nullable=True),
        sa.Column("country_id", sa.BigInteger(), sa.ForeignKey("country.id"), nullable=True),
    )
    op.create_table(
        "season",
        _id_column(),
        sa.Column("league_id", sa.BigInteger(), sa.ForeignKey("league.id"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("current", sa.Boolean(), nullable=False),
        sa.Column("coverage", _JSON, nullable=True),
        sa.UniqueConstraint("league_id", "year"),
    )
    op.create_table(
        "venue",
        _id_column(),
        sa.Column("api_venue_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("city", sa.String(120), nullable=True),
    )
    op.create_table(
        "team",
        _id_column(),
        sa.Column("api_team_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(8), nullable=True),
        sa.Column("country", sa.String(120), nullable=True),
        sa.Column("founded", sa.Integer(), nullable=True),
        sa.Column("logo", sa.String(400), nullable=True),
        sa.Column("venue_id", sa.BigInteger(), sa.ForeignKey("venue.id"), nullable=True),
    )
    op.create_table(
        "player",
        _id_column(),
        sa.Column("api_player_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=True),
    )
    op.create_table(
        "fixture",
        _id_column(),
        sa.Column("api_fixture_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("league_id", sa.BigInteger(), sa.ForeignKey("league.id"), nullable=False),
        sa.Column("season_id", sa.BigInteger(), sa.ForeignKey("season.id"), nullable=False),
        sa.Column("kickoff_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_short", sa.String(8), nullable=False),
        sa.Column("elapsed", sa.Integer(), nullable=True),
        sa.Column("home_team_id", sa.BigInteger(), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("away_team_id", sa.BigInteger(), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("home_goals", sa.Integer(), nullable=True),
        sa.Column("away_goals", sa.Integer(), nullable=True),
        sa.Column("referee", sa.String(200), nullable=True),
    )
    op.create_table(
        "fixture_event",
        _id_column(),
        sa.Column("fixture_id", sa.BigInteger(), sa.ForeignKey("fixture.id"), nullable=False),
        sa.Column("event_hash", sa.String(40), nullable=False),
        sa.Column("elapsed", sa.Integer(), nullable=False),
        sa.Column("extra", sa.Integer(), nullable=True),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("player_id", sa.BigInteger(), sa.ForeignKey("player.id"), nullable=True),
        sa.Column("assist_id", sa.BigInteger(), sa.ForeignKey("player.id"), nullable=True),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("detail", sa.String(200), nullable=False),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fixture_id", "event_hash"),
    )
    op.create_table(
        "commentator",
        _id_column(),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("style", _JSON, nullable=False),
    )
    op.create_table(
        "commentary_message",
        _id_column(),
        sa.Column("fixture_id", sa.BigInteger(), sa.ForeignKey("fixture.id"), nullable=False),
        sa.Column(
            "commentator_id", sa.BigInteger(), sa.ForeignKey("commentator.id"), nullable=False
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "triggering_event_id",
            sa.BigInteger(),
            sa.ForeignKey("fixture_event.id"),
            nullable=True,
        ),
        sa.Column(
            "in_reply_to",
            sa.BigInteger(),
            sa.ForeignKey("commentary_message.id"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("usage", _JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "api_request_log",
        _id_column(),
        sa.Column("endpoint", sa.String(120), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requests_remaining", sa.Integer(), nullable=True),
    )

    if _is_postgres():
        for statement in _TRIGGER_STATEMENTS:
            op.execute(statement)


def downgrade() -> None:
    if _is_postgres():
        for statement in _DROP_TRIGGER_STATEMENTS:
            op.execute(statement)
    for table in (
        "api_request_log",
        "commentary_message",
        "commentator",
        "fixture_event",
        "fixture",
        "player",
        "team",
        "venue",
        "season",
        "league",
        "country",
    ):
        op.drop_table(table)
