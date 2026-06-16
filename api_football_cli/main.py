"""Composition root: builders plus process-specific runtime entrypoints."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Protocol, cast

import anthropic
import asyncpg
import httpx
import uvicorn
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from api_football_cli.adapters.inbound.web.app import WebDeps, create_app
from api_football_cli.adapters.outbound.apifootball.http import HttpxFootballApi
from api_football_cli.adapters.outbound.messaging.postgres import (
    NotifyConnection,
    PostgresListenNotifyBus,
)
from api_football_cli.adapters.outbound.model.anthropic import AnthropicCommentaryModel
from api_football_cli.adapters.outbound.model.fake import DEFAULT_FAKE_LINES, FakeModel
from api_football_cli.adapters.outbound.persistence.engine import create_engine_and_sessions
from api_football_cli.adapters.outbound.persistence.repositories import (
    SqlApiRequestLogRepository,
    SqlCommentaryRepository,
    SqlCommentatorRepository,
    SqlEventRepository,
    SqlFixtureRepository,
    SqlReferenceRepository,
)
from api_football_cli.application.ports.commentary_model import CommentaryModel
from api_football_cli.application.ports.football_api import FootballApi
from api_football_cli.application.ports.repositories import FixtureRepository, NotFoundError
from api_football_cli.application.services.generate_commentary import (
    CommentaryWorker,
    GenerateCommentaryRound,
)
from api_football_cli.application.services.ingest_events import IngestFixtureEvents
from api_football_cli.application.services.stream_commentary import StreamCommentary
from api_football_cli.application.services.sync_reference import (
    FixtureSyncReport,
    LeagueSyncReport,
    SyncReferenceData,
    TeamSyncReport,
)
from api_football_cli.config import (
    ApiFootballConfig,
    ConfigError,
    DatabaseConfig,
    ModelConfig,
)
from api_football_cli.domain.entities import (
    AccountStatus,
    Commentator,
    Fixture,
    FrozenModel,
)
from api_football_cli.domain.personas import PERSONAS

# Director policy cap (architecture §7): one exchange per round —
# play-by-play reacts, the colour commentator responds.
MAX_MESSAGES_PER_ROUND = 2
SSE_PING_SECONDS = 15.0
WORKER_LOCK_NAMESPACE = 0x0AFC

# The frontend ships at the repository root beside the package directory.
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


class WebConfig(FrozenModel):
    host: str
    port: int
    database: DatabaseConfig
    frontend_dir: Path | None
    sse_ping_seconds: float


class IngestConfig(FrozenModel):
    api_fixture_id: int
    interval_seconds: float
    database: DatabaseConfig
    apifootball: ApiFootballConfig


class WorkerConfig(FrozenModel):
    api_fixture_id: int
    database: DatabaseConfig
    model: ModelConfig
    max_messages_per_round: int


class DevConfig(FrozenModel):
    api_fixture_id: int
    interval_seconds: float
    host: str
    port: int
    database: DatabaseConfig
    model: ModelConfig
    apifootball: ApiFootballConfig
    frontend_dir: Path | None
    sse_ping_seconds: float
    max_messages_per_round: int


def build_commentary_model(config: ModelConfig) -> CommentaryModel:
    if config.provider == "fake":
        return FakeModel(lines=DEFAULT_FAKE_LINES)
    if config.anthropic is None:
        raise ConfigError("provider 'anthropic' selected but no anthropic config present")
    client = anthropic.AsyncAnthropic(api_key=config.anthropic.api_key)
    return AnthropicCommentaryModel(
        client=client,
        model=config.anthropic.model,
        max_tokens=config.anthropic.max_tokens,
    )


def build_live_api(config: ApiFootballConfig) -> HttpxFootballApi:
    return HttpxFootballApi(
        api_key=config.key,
        base_url=config.base_url,
        http_client=httpx.AsyncClient(timeout=httpx.Timeout(30.0)),
    )


async def close_football_api(api: FootballApi) -> None:
    if isinstance(api, HttpxFootballApi):
        await api.aclose()


def _postgres_connector(dsn: str):  # pragma: no cover - thin asyncpg wrapper
    async def connect() -> NotifyConnection:
        connection = await asyncpg.connect(dsn)
        return cast(NotifyConnection, connection)

    return connect


class ServerHandle(Protocol):
    """The slice of uvicorn.Server the runtime depends on (swappable in tests)."""

    async def serve(self) -> None: ...


class NoSignalServer(uvicorn.Server):
    """uvicorn server that leaves signal handling to asyncio.run / the CLI."""

    def install_signal_handlers(self) -> None:  # pragma: no cover - trivial
        return None


def build_server(app: FastAPI, *, host: str, port: int, log_level: str) -> NoSignalServer:
    return NoSignalServer(uvicorn.Config(app=app, host=host, port=port, log_level=log_level))


async def serve_runtime(
    *,
    ingestion: Coroutine[None, None, object],
    worker: Coroutine[None, None, object],
    server: Coroutine[None, None, object],
) -> None:
    """Supervise local dev tasks; one crash takes the all-in-one process down."""
    async with asyncio.TaskGroup() as group:
        group.create_task(ingestion, name="ingestion")
        group.create_task(worker, name="commentary-worker")
        group.create_task(server, name="web-server")


async def run_web(config: WebConfig) -> None:
    config.database.require_postgres()
    async with AsyncExitStack() as stack:
        engine, sessions = create_engine_and_sessions(config.database.url)
        stack.push_async_callback(engine.dispose)

        fixtures = SqlFixtureRepository(sessions)
        commentary = SqlCommentaryRepository(sessions)
        bus = PostgresListenNotifyBus(_postgres_connector(config.database.notify_dsn()))
        await bus.start()
        stack.push_async_callback(bus.close)

        stream = StreamCommentary(commentary=commentary, bus=bus)
        deps = WebDeps(
            fixtures=fixtures,
            events=SqlEventRepository(sessions),
            commentary=commentary,
            commentators=SqlCommentatorRepository(sessions),
            stream=stream,
            sse_ping_seconds=config.sse_ping_seconds,
        )
        app = create_app(deps=deps, frontend_dir=config.frontend_dir)
        server = build_server(app, host=config.host, port=config.port, log_level="info")
        await server.serve()


async def run_ingest(config: IngestConfig) -> Fixture:
    config.database.require_postgres()
    async with AsyncExitStack() as stack:
        engine, sessions = create_engine_and_sessions(config.database.url)
        stack.push_async_callback(engine.dispose)

        api = build_live_api(config.apifootball)
        stack.push_async_callback(close_football_api, api)

        service = IngestFixtureEvents(
            api=api,
            fixtures=SqlFixtureRepository(sessions),
            events=SqlEventRepository(sessions),
            request_log=SqlApiRequestLogRepository(sessions),
            interval_seconds=config.interval_seconds,
        )
        return await service.run(config.api_fixture_id)


async def run_worker(config: WorkerConfig) -> None:
    config.database.require_postgres()
    if config.max_messages_per_round <= 0:
        raise ValueError(
            f"max_messages_per_round must be positive, got {config.max_messages_per_round}"
        )

    async with AsyncExitStack() as stack:
        engine, sessions = create_engine_and_sessions(config.database.url)
        stack.push_async_callback(engine.dispose)

        fixtures = SqlFixtureRepository(sessions)
        fixture = await _require_prepared_fixture(
            fixtures=fixtures,
            api_fixture_id=config.api_fixture_id,
        )

        lock_connection = await engine.connect()
        stack.push_async_callback(lock_connection.close)
        await _acquire_worker_lock(connection=lock_connection, fixture=fixture)

        bus = PostgresListenNotifyBus(_postgres_connector(config.database.notify_dsn()))
        await bus.start()
        stack.push_async_callback(bus.close)

        commentator_repo = SqlCommentatorRepository(sessions)
        commentators: list[Commentator] = [
            await commentator_repo.upsert(seed) for seed in PERSONAS
        ]
        rounds = GenerateCommentaryRound(
            fixtures=fixtures,
            events=SqlEventRepository(sessions),
            commentary=SqlCommentaryRepository(sessions),
            model=build_commentary_model(config.model),
            commentators=commentators,
            max_messages_per_round=config.max_messages_per_round,
        )
        worker = CommentaryWorker(bus=bus, rounds=rounds, fixture_id=fixture.id)
        await worker.run()


async def run_dev(config: DevConfig) -> None:
    config.database.require_postgres()
    async with AsyncExitStack() as stack:
        engine, sessions = create_engine_and_sessions(config.database.url)
        stack.push_async_callback(engine.dispose)

        fixtures = SqlFixtureRepository(sessions)
        events = SqlEventRepository(sessions)
        commentary = SqlCommentaryRepository(sessions)
        commentator_repo = SqlCommentatorRepository(sessions)

        bus = PostgresListenNotifyBus(_postgres_connector(config.database.notify_dsn()))
        await bus.start()
        stack.push_async_callback(bus.close)

        api = build_live_api(config.apifootball)
        stack.push_async_callback(close_football_api, api)
        model = build_commentary_model(config.model)

        commentators: list[Commentator] = [
            await commentator_repo.upsert(seed) for seed in PERSONAS
        ]
        # Prime the fixture row so the worker and web layer know our fixture id
        # before the first poll loop iteration.
        fixture = await fixtures.upsert_snapshot(await api.fixture(config.api_fixture_id))

        ingest = IngestFixtureEvents(
            api=api,
            fixtures=fixtures,
            events=events,
            request_log=SqlApiRequestLogRepository(sessions),
            interval_seconds=config.interval_seconds,
        )
        rounds = GenerateCommentaryRound(
            fixtures=fixtures,
            events=events,
            commentary=commentary,
            model=model,
            commentators=commentators,
            max_messages_per_round=config.max_messages_per_round,
        )
        worker = CommentaryWorker(bus=bus, rounds=rounds, fixture_id=fixture.id)
        stream = StreamCommentary(commentary=commentary, bus=bus)
        deps = WebDeps(
            fixtures=fixtures,
            events=events,
            commentary=commentary,
            commentators=commentator_repo,
            stream=stream,
            sse_ping_seconds=config.sse_ping_seconds,
        )
        app = create_app(deps=deps, frontend_dir=config.frontend_dir)
        server = build_server(app, host=config.host, port=config.port, log_level="info")

        await serve_runtime(
            ingestion=ingest.run(config.api_fixture_id),
            worker=worker.run(),
            server=server.serve(),
        )


async def _require_prepared_fixture(
    *,
    fixtures: FixtureRepository,
    api_fixture_id: int,
) -> Fixture:
    try:
        return await fixtures.get_by_api_fixture_id(api_fixture_id)
    except NotFoundError as exc:
        raise RuntimeError(
            f"api-football fixture {api_fixture_id} is not prepared in the database; "
            "run ingestion before starting the worker"
        ) from exc


async def _acquire_worker_lock(
    *, connection: AsyncConnection, fixture: Fixture
) -> None:
    locked = await connection.scalar(
        text("SELECT pg_try_advisory_lock(:key)"),
        {"key": _worker_lock_key(fixture.id)},
    )
    if locked is not True:
        raise RuntimeError(
            f"commentary worker for fixture {fixture.id} "
            f"(api-football {fixture.api_fixture_id}) is already running"
        )


def _worker_lock_key(fixture_id: int) -> int:
    return (WORKER_LOCK_NAMESPACE << 32) + fixture_id


async def run_status(*, api: FootballApi) -> AccountStatus:
    return await api.account_status()


async def run_sync_leagues(
    *, api: FootballApi, database_url: str, season: int
) -> LeagueSyncReport:
    async with AsyncExitStack() as stack:
        engine, sessions = create_engine_and_sessions(database_url)
        stack.push_async_callback(engine.dispose)
        service = SyncReferenceData(
            api=api,
            reference=SqlReferenceRepository(sessions),
            fixtures=SqlFixtureRepository(sessions),
        )
        return await service.sync_leagues(season=season)


async def run_sync_teams(
    *, api: FootballApi, database_url: str, league_api_id: int, season: int
) -> TeamSyncReport:
    async with AsyncExitStack() as stack:
        engine, sessions = create_engine_and_sessions(database_url)
        stack.push_async_callback(engine.dispose)
        service = SyncReferenceData(
            api=api,
            reference=SqlReferenceRepository(sessions),
            fixtures=SqlFixtureRepository(sessions),
        )
        return await service.sync_teams(league_api_id=league_api_id, season=season)


async def run_sync_fixtures(
    *, api: FootballApi, database_url: str, league_api_id: int, season: int
) -> FixtureSyncReport:
    async with AsyncExitStack() as stack:
        engine, sessions = create_engine_and_sessions(database_url)
        stack.push_async_callback(engine.dispose)
        service = SyncReferenceData(
            api=api,
            reference=SqlReferenceRepository(sessions),
            fixtures=SqlFixtureRepository(sessions),
        )
        return await service.sync_fixtures(league_api_id=league_api_id, season=season)
