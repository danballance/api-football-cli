"""SQLAlchemy implementations of the repository ports.

Each method is one unit of work (session + transaction). Upserts use
select-then-insert/update keyed on the api_*_id reference columns; the
append-only event log additionally relies on UNIQUE(fixture_id, event_hash)
to stay correct under races.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api_football_cli.adapters.outbound.persistence.engine import SessionFactory
from api_football_cli.adapters.outbound.persistence.tables import (
    ApiRequestLogRow,
    CommentaryMessageRow,
    CommentatorRow,
    CountryRow,
    FixtureEventRow,
    FixtureRow,
    LeagueRow,
    PlayerRow,
    SeasonRow,
    TeamRow,
    VenueRow,
)
from api_football_cli.application.ports.repositories import (
    CommentaryRepository,
    CommentatorRepository,
    EventRepository,
    FixtureRepository,
    NotFoundError,
    ReferenceRepository, ApiRequestLogRepository,
)
from api_football_cli.domain.entities import (
    CommentaryDraft,
    CommentaryMessage,
    Commentator,
    CommentatorRole,
    CommentatorStyle,
    Fixture,
    FixtureSnapshot,
    FixtureStatus,
    League,
    LeagueRef,
    ObservedEvent,
    PlayerRef,
    Season,
    StoredFixtureEvent,
    StoredTeam,
    TeamProfile,
    TeamRef,
    Venue,
)
from api_football_cli.domain.events import event_hash
from api_football_cli.domain.personas import PersonaSeed


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    """SQLite drops tzinfo; everything we store is UTC, so restore it."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def _ensure_country(session: AsyncSession, *, name: str, code: str | None) -> int:
    row = await session.scalar(select(CountryRow).where(CountryRow.name == name))
    if row is None:
        row = CountryRow(name=name, code=code)
        session.add(row)
        await session.flush()
    elif code is not None and row.code != code:
        row.code = code
    return row.id


async def _ensure_league_minimal(session: AsyncSession, league: LeagueRef) -> int:
    row = await session.scalar(
        select(LeagueRow).where(LeagueRow.api_league_id == league.api_league_id)
    )
    if row is None:
        country_id = None
        if league.country is not None:
            country_id = await _ensure_country(session, name=league.country, code=None)
        row = LeagueRow(
            api_league_id=league.api_league_id,
            name=league.name,
            type=None,
            country_id=country_id,
        )
        session.add(row)
        await session.flush()
    return row.id


async def _ensure_season_minimal(session: AsyncSession, *, league_id: int, year: int) -> int:
    row = await session.scalar(
        select(SeasonRow).where(SeasonRow.league_id == league_id, SeasonRow.year == year)
    )
    if row is None:
        row = SeasonRow(league_id=league_id, year=year, current=False, coverage=None)
        session.add(row)
        await session.flush()
    return row.id


async def _ensure_team_minimal(session: AsyncSession, team: TeamRef) -> int:
    row = await session.scalar(select(TeamRow).where(TeamRow.api_team_id == team.api_team_id))
    if row is None:
        row = TeamRow(
            api_team_id=team.api_team_id,
            name=team.name,
            code=None,
            country=None,
            founded=None,
            logo=None,
            venue_id=None,
        )
        session.add(row)
        await session.flush()
    elif row.name != team.name:
        row.name = team.name
    return row.id


async def _ensure_player(session: AsyncSession, player: PlayerRef | None) -> int | None:
    if player is None or player.api_player_id is None:
        return None
    row = await session.scalar(
        select(PlayerRow).where(PlayerRow.api_player_id == player.api_player_id)
    )
    if row is None:
        row = PlayerRow(api_player_id=player.api_player_id, name=player.name)
        session.add(row)
        await session.flush()
    elif player.name is not None and row.name != player.name:
        row.name = player.name
    return row.id


async def _fixture_to_domain(session: AsyncSession, row: FixtureRow) -> Fixture:
    league_row = await session.get(LeagueRow, row.league_id)
    season_row = await session.get(SeasonRow, row.season_id)
    home_row = await session.get(TeamRow, row.home_team_id)
    away_row = await session.get(TeamRow, row.away_team_id)
    if league_row is None or season_row is None or home_row is None or away_row is None:
        raise NotFoundError(f"fixture {row.id} references missing rows")
    country_name: str | None = None
    if league_row.country_id is not None:
        country_row = await session.get(CountryRow, league_row.country_id)
        country_name = country_row.name if country_row is not None else None
    return Fixture(
        id=row.id,
        api_fixture_id=row.api_fixture_id,
        league=LeagueRef(
            api_league_id=league_row.api_league_id,
            name=league_row.name,
            country=country_name,
            season=season_row.year,
        ),
        kickoff=_aware(row.kickoff_ts),
        status=FixtureStatus(row.status_short),
        elapsed=row.elapsed,
        home=StoredTeam(id=home_row.id, api_team_id=home_row.api_team_id, name=home_row.name),
        away=StoredTeam(id=away_row.id, api_team_id=away_row.api_team_id, name=away_row.name),
        home_goals=row.home_goals,
        away_goals=row.away_goals,
        referee=row.referee,
    )


class SqlFixtureRepository(FixtureRepository):
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def upsert_snapshot(self, snapshot: FixtureSnapshot) -> Fixture:
        async with self._sessions() as session:
            async with session.begin():
                league_id = await _ensure_league_minimal(session, snapshot.league)
                season_id = await _ensure_season_minimal(
                    session, league_id=league_id, year=snapshot.league.season
                )
                home_id = await _ensure_team_minimal(session, snapshot.home)
                away_id = await _ensure_team_minimal(session, snapshot.away)

                row = await session.scalar(
                    select(FixtureRow).where(
                        FixtureRow.api_fixture_id == snapshot.api_fixture_id
                    )
                )
                if row is None:
                    row = FixtureRow(
                        api_fixture_id=snapshot.api_fixture_id,
                        league_id=league_id,
                        season_id=season_id,
                        kickoff_ts=snapshot.kickoff,
                        status_short=snapshot.status.value,
                        elapsed=snapshot.elapsed,
                        home_team_id=home_id,
                        away_team_id=away_id,
                        home_goals=snapshot.home_goals,
                        away_goals=snapshot.away_goals,
                        referee=snapshot.referee,
                    )
                    session.add(row)
                    await session.flush()
                else:
                    row.league_id = league_id
                    row.season_id = season_id
                    row.kickoff_ts = snapshot.kickoff
                    row.status_short = snapshot.status.value
                    row.elapsed = snapshot.elapsed
                    row.home_team_id = home_id
                    row.away_team_id = away_id
                    row.home_goals = snapshot.home_goals
                    row.away_goals = snapshot.away_goals
                    row.referee = snapshot.referee
                return await _fixture_to_domain(session, row)

    async def get(self, fixture_id: int) -> Fixture:
        async with self._sessions() as session:
            row = await session.get(FixtureRow, fixture_id)
            if row is None:
                raise NotFoundError(f"fixture {fixture_id} not found")
            return await _fixture_to_domain(session, row)

    async def list_all(self) -> list[Fixture]:
        async with self._sessions() as session:
            rows = (await session.scalars(select(FixtureRow).order_by(FixtureRow.id))).all()
            return [await _fixture_to_domain(session, row) for row in rows]


class SqlEventRepository(EventRepository):
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def insert_if_new(
        self, *, fixture_id: int, event: ObservedEvent
    ) -> StoredFixtureEvent | None:
        digest = event_hash(event)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    existing = await session.scalar(
                        select(FixtureEventRow.id).where(
                            FixtureEventRow.fixture_id == fixture_id,
                            FixtureEventRow.event_hash == digest,
                        )
                    )
                    if existing is not None:
                        return None
                    row = FixtureEventRow(
                        fixture_id=fixture_id,
                        event_hash=digest,
                        elapsed=event.elapsed,
                        extra=event.extra,
                        team_id=await _ensure_team_minimal(session, event.team),
                        player_id=await _ensure_player(session, event.player),
                        assist_id=await _ensure_player(session, event.assist),
                        type=event.type,
                        detail=event.detail,
                        comments=event.comments,
                        created_at=_now(),
                    )
                    session.add(row)
                    await session.flush()
                    return StoredFixtureEvent(
                        id=row.id,
                        fixture_id=fixture_id,
                        event_hash=digest,
                        event=event,
                        created_at=_aware(row.created_at),
                    )
        except IntegrityError:
            # Lost a race on UNIQUE(fixture_id, event_hash): already appended.
            return None

    async def _select_events(
        self, session: AsyncSession, *, fixture_id: int, after_event_id: int
    ) -> list[StoredFixtureEvent]:
        player_alias = aliased(PlayerRow)
        assist_alias = aliased(PlayerRow)
        stmt = (
            select(FixtureEventRow, TeamRow, player_alias, assist_alias)
            .join(TeamRow, FixtureEventRow.team_id == TeamRow.id)
            .outerjoin(player_alias, FixtureEventRow.player_id == player_alias.id)
            .outerjoin(assist_alias, FixtureEventRow.assist_id == assist_alias.id)
            .where(FixtureEventRow.fixture_id == fixture_id, FixtureEventRow.id > after_event_id)
            .order_by(FixtureEventRow.id)
        )
        results = (await session.execute(stmt)).all()
        stored: list[StoredFixtureEvent] = []
        for event_row, team_row, player_row, assist_row in results:
            event = ObservedEvent(
                elapsed=event_row.elapsed,
                extra=event_row.extra,
                team=TeamRef(api_team_id=team_row.api_team_id, name=team_row.name),
                player=_player_ref(player_row),
                assist=_player_ref(assist_row),
                type=event_row.type,
                detail=event_row.detail,
                comments=event_row.comments,
            )
            stored.append(
                StoredFixtureEvent(
                    id=event_row.id,
                    fixture_id=event_row.fixture_id,
                    event_hash=event_row.event_hash,
                    event=event,
                    created_at=_aware(event_row.created_at),
                )
            )
        return stored

    async def list_for_fixture(self, fixture_id: int) -> list[StoredFixtureEvent]:
        async with self._sessions() as session:
            return await self._select_events(session, fixture_id=fixture_id, after_event_id=0)

    async def list_after(
        self, *, fixture_id: int, after_event_id: int
    ) -> list[StoredFixtureEvent]:
        async with self._sessions() as session:
            return await self._select_events(
                session, fixture_id=fixture_id, after_event_id=after_event_id
            )


def _player_ref(row: PlayerRow | None) -> PlayerRef | None:
    if row is None:
        return None
    return PlayerRef(api_player_id=row.api_player_id, name=row.name)


class SqlCommentatorRepository(CommentatorRepository):
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def upsert(self, seed: PersonaSeed) -> Commentator:
        async with self._sessions() as session:
            async with session.begin():
                row = await session.scalar(
                    select(CommentatorRow).where(CommentatorRow.name == seed.name)
                )
                if row is None:
                    row = CommentatorRow(
                        name=seed.name,
                        role=seed.role.value,
                        system_prompt=seed.system_prompt,
                        style=seed.style.model_dump(),
                    )
                    session.add(row)
                    await session.flush()
                else:
                    row.role = seed.role.value
                    row.system_prompt = seed.system_prompt
                    row.style = seed.style.model_dump()
                return _commentator_to_domain(row)

    async def list_all(self) -> list[Commentator]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(select(CommentatorRow).order_by(CommentatorRow.id))
            ).all()
            return [_commentator_to_domain(row) for row in rows]


def _commentator_to_domain(row: CommentatorRow) -> Commentator:
    return Commentator(
        id=row.id,
        name=row.name,
        role=CommentatorRole(row.role),
        system_prompt=row.system_prompt,
        style=CommentatorStyle.model_validate(row.style),
    )


class SqlCommentaryRepository(CommentaryRepository):
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def insert(self, draft: CommentaryDraft) -> CommentaryMessage:
        async with self._sessions() as session:
            async with session.begin():
                row = CommentaryMessageRow(
                    fixture_id=draft.fixture_id,
                    commentator_id=draft.commentator_id,
                    text=draft.text,
                    triggering_event_id=draft.triggering_event_id,
                    in_reply_to=draft.in_reply_to,
                    provider=draft.provider,
                    model=draft.model,
                    usage=draft.usage,
                    created_at=_now(),
                )
                session.add(row)
                await session.flush()
                return _message_to_domain(row)

    async def list_for_fixture(self, fixture_id: int) -> list[CommentaryMessage]:
        return await self.list_after(fixture_id=fixture_id, after_message_id=0)

    async def list_after(
        self, *, fixture_id: int, after_message_id: int
    ) -> list[CommentaryMessage]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(CommentaryMessageRow)
                    .where(
                        CommentaryMessageRow.fixture_id == fixture_id,
                        CommentaryMessageRow.id > after_message_id,
                    )
                    .order_by(CommentaryMessageRow.id)
                )
            ).all()
            return [_message_to_domain(row) for row in rows]

    async def last_triggering_event_id(self, fixture_id: int) -> int:
        async with self._sessions() as session:
            value = await session.scalar(
                select(func.max(CommentaryMessageRow.triggering_event_id)).where(
                    CommentaryMessageRow.fixture_id == fixture_id
                )
            )
            return value if value is not None else 0


def _message_to_domain(row: CommentaryMessageRow) -> CommentaryMessage:
    return CommentaryMessage(
        id=row.id,
        fixture_id=row.fixture_id,
        commentator_id=row.commentator_id,
        text=row.text,
        triggering_event_id=row.triggering_event_id,
        in_reply_to=row.in_reply_to,
        provider=row.provider,
        model=row.model,
        usage=row.usage,
        created_at=_aware(row.created_at),
    )


class SqlReferenceRepository(ReferenceRepository):
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def upsert_league(self, league: League) -> int:
        async with self._sessions() as session:
            async with session.begin():
                country_id = await _ensure_country(
                    session, name=league.country.name, code=league.country.code
                )
                row = await session.scalar(
                    select(LeagueRow).where(LeagueRow.api_league_id == league.api_league_id)
                )
                if row is None:
                    row = LeagueRow(
                        api_league_id=league.api_league_id,
                        name=league.name,
                        type=league.type,
                        country_id=country_id,
                    )
                    session.add(row)
                    await session.flush()
                else:
                    row.name = league.name
                    row.type = league.type
                    row.country_id = country_id
                return row.id

    async def upsert_season(self, *, league_id: int, season: Season) -> int:
        async with self._sessions() as session:
            async with session.begin():
                row = await session.scalar(
                    select(SeasonRow).where(
                        SeasonRow.league_id == league_id, SeasonRow.year == season.year
                    )
                )
                if row is None:
                    row = SeasonRow(
                        league_id=league_id,
                        year=season.year,
                        current=season.current,
                        coverage=season.coverage.model_dump(),
                    )
                    session.add(row)
                    await session.flush()
                else:
                    row.current = season.current
                    row.coverage = season.coverage.model_dump()
                return row.id

    async def upsert_venue(self, venue: Venue) -> int:
        async with self._sessions() as session:
            async with session.begin():
                row = await session.scalar(
                    select(VenueRow).where(VenueRow.api_venue_id == venue.api_venue_id)
                )
                if row is None:
                    row = VenueRow(
                        api_venue_id=venue.api_venue_id, name=venue.name, city=venue.city
                    )
                    session.add(row)
                    await session.flush()
                else:
                    row.name = venue.name
                    row.city = venue.city
                return row.id

    async def upsert_team(self, *, team: TeamProfile, venue_id: int | None) -> int:
        async with self._sessions() as session:
            async with session.begin():
                row = await session.scalar(
                    select(TeamRow).where(TeamRow.api_team_id == team.api_team_id)
                )
                if row is None:
                    row = TeamRow(
                        api_team_id=team.api_team_id,
                        name=team.name,
                        code=team.code,
                        country=team.country,
                        founded=team.founded,
                        logo=team.logo,
                        venue_id=venue_id,
                    )
                    session.add(row)
                    await session.flush()
                else:
                    row.name = team.name
                    row.code = team.code
                    row.country = team.country
                    row.founded = team.founded
                    row.logo = team.logo
                    row.venue_id = venue_id
                return row.id


class SqlApiRequestLogRepository(ApiRequestLogRepository):
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def record(self, *, endpoint: str, requests_remaining: int | None) -> None:
        async with self._sessions() as session:
            async with session.begin():
                session.add(
                    ApiRequestLogRow(
                        endpoint=endpoint,
                        ts=_now(),
                        requests_remaining=requests_remaining,
                    )
                )
