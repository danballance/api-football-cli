"""Composition root (architecture §3).

Reads explicit config, builds the adapters, injects them into the application
services, and supervises the three runtime tasks — ingestion, commentary
worker, web server — in a single asyncio TaskGroup. Any task's unhandled
error cancels its siblings and takes the process down (fail fast, §1).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Protocol, cast

import anthropic
import asyncpg
import httpx
import uvicorn
from fastapi import FastAPI

from api_football_cli.adapters.inbound.web.app import WebDeps, create_app
from api_football_cli.adapters.outbound.apifootball.fake import FakeFootballApi, ReplayFile
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
    TERMINAL_STATUSES,
    AccountStatus,
    Commentator,
    FrozenModel,
)
from api_football_cli.domain.personas import PERSONAS

# Director policy cap (architecture §7): one exchange per round —
# play-by-play reacts, the colour commentator responds.
MAX_MESSAGES_PER_ROUND = 2
SSE_PING_SECONDS = 15.0

# The frontend ships with the repository, two levels above the package dir.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


class RecordError(RuntimeError):
    """Raised when a fixture cannot be recorded for replay."""


class ServeConfig(FrozenModel):
    api_fixture_id: int
    interval_seconds: float
    host: str
    port: int
    database: DatabaseConfig
    model: ModelConfig
    apifootball: ApiFootballConfig | None
    quota_floor: int | None
    replay_path: Path | None
    replay_step_minutes: int | None
    frontend_dir: Path | None


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


def build_replay_api(*, replay_path: Path, step_minutes: int) -> FakeFootballApi:
    return FakeFootballApi(
        replay=ReplayFile.load(replay_path), minutes_per_poll=step_minutes
    )


def build_serve_api(config: ServeConfig) -> FootballApi:
    if config.replay_path is not None:
        if config.replay_step_minutes is None:
            raise ConfigError("replay mode requires an explicit replay step")
        return build_replay_api(
            replay_path=config.replay_path, step_minutes=config.replay_step_minutes
        )
    if config.apifootball is None:
        raise ConfigError("live mode requires api-football configuration")
    return build_live_api(config.apifootball)


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
    """Supervise the three runtime tasks; one crash takes everything down."""
    async with asyncio.TaskGroup() as group:
        group.create_task(ingestion, name="ingestion")
        group.create_task(worker, name="commentary-worker")
        group.create_task(server, name="web-server")


async def run_serve(config: ServeConfig) -> None:
    config.database.require_postgres()
    engine, sessions = create_engine_and_sessions(config.database.url)
    fixtures = SqlFixtureRepository(sessions)
    events = SqlEventRepository(sessions)
    commentary = SqlCommentaryRepository(sessions)
    commentator_repo = SqlCommentatorRepository(sessions)
    request_log = SqlApiRequestLogRepository(sessions)

    bus = PostgresListenNotifyBus(_postgres_connector(config.database.notify_dsn()))
    await bus.start()
    api = build_serve_api(config)
    model = build_commentary_model(config.model)

    try:
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
            request_log=request_log,
            interval_seconds=config.interval_seconds,
            quota_floor=config.quota_floor,
        )
        rounds = GenerateCommentaryRound(
            fixtures=fixtures,
            events=events,
            commentary=commentary,
            model=model,
            commentators=commentators,
            max_messages_per_round=MAX_MESSAGES_PER_ROUND,
        )
        worker = CommentaryWorker(bus=bus, rounds=rounds, fixture_id=fixture.id)
        stream = StreamCommentary(commentary=commentary, bus=bus)
        deps = WebDeps(
            fixtures=fixtures,
            events=events,
            commentary=commentary,
            commentators=commentator_repo,
            stream=stream,
            sse_ping_seconds=SSE_PING_SECONDS,
        )
        app = create_app(deps=deps, frontend_dir=config.frontend_dir)
        server = build_server(app, host=config.host, port=config.port, log_level="info")

        await serve_runtime(
            ingestion=ingest.run(config.api_fixture_id),
            worker=worker.run(),
            server=server.serve(),
        )
    finally:
        await bus.close()
        if isinstance(api, HttpxFootballApi):
            await api.aclose()
        await engine.dispose()


async def run_record(*, api: FootballApi, api_fixture_id: int, output: Path) -> ReplayFile:
    """Record a finished fixture into a replay file (architecture §10)."""
    snapshot = await api.fixture(api_fixture_id)
    if snapshot.status not in TERMINAL_STATUSES:
        raise RecordError(
            f"fixture {api_fixture_id} has status {snapshot.status.value!r}; "
            "only finished fixtures can be recorded for replay"
        )
    events = await api.fixtures_events(api_fixture_id)
    replay = ReplayFile(fixture=snapshot, events=tuple(events))
    replay.dump(output)
    return replay


async def run_status(*, api: FootballApi) -> AccountStatus:
    return await api.account_status()


async def run_sync_leagues(
    *, api: FootballApi, database_url: str, season: int
) -> LeagueSyncReport:
    engine, sessions = create_engine_and_sessions(database_url)
    try:
        service = SyncReferenceData(
            api=api,
            reference=SqlReferenceRepository(sessions),
            fixtures=SqlFixtureRepository(sessions),
        )
        return await service.sync_leagues(season=season)
    finally:
        await engine.dispose()


async def run_sync_teams(
    *, api: FootballApi, database_url: str, league_api_id: int, season: int
) -> TeamSyncReport:
    engine, sessions = create_engine_and_sessions(database_url)
    try:
        service = SyncReferenceData(
            api=api,
            reference=SqlReferenceRepository(sessions),
            fixtures=SqlFixtureRepository(sessions),
        )
        return await service.sync_teams(league_api_id=league_api_id, season=season)
    finally:
        await engine.dispose()


async def run_sync_fixtures(
    *, api: FootballApi, database_url: str, league_api_id: int, season: int
) -> FixtureSyncReport:
    engine, sessions = create_engine_and_sessions(database_url)
    try:
        service = SyncReferenceData(
            api=api,
            reference=SqlReferenceRepository(sessions),
            fixtures=SqlFixtureRepository(sessions),
        )
        return await service.sync_fixtures(league_api_id=league_api_id, season=season)
    finally:
        await engine.dispose()
