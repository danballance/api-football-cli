"""Composition root: builders, record/status flows, TaskGroup supervision."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from api_football_cli.adapters.outbound.apifootball.fake import FakeFootballApi, ReplayFile
from api_football_cli.adapters.outbound.apifootball.http import HttpxFootballApi
from api_football_cli.adapters.outbound.model.anthropic import AnthropicCommentaryModel
from api_football_cli.adapters.outbound.model.fake import FakeModel
from api_football_cli.config import (
    AnthropicConfig,
    ApiFootballConfig,
    ConfigError,
    DatabaseConfig,
    ModelConfig,
)
from api_football_cli.domain.entities import AccountStatus, FixtureStatus
from api_football_cli.main import (
    FRONTEND_DIR,
    RecordError,
    ServeConfig,
    build_commentary_model,
    build_serve_api,
    run_record,
    run_status,
    serve_runtime,
)

from tests.factories import make_event, make_snapshot
from tests.fakes import StubFootballApi

DB = DatabaseConfig(url="postgresql+asyncpg://app:pw@localhost/afc")


def make_serve_config(
    *,
    replay_path: Path | None,
    replay_step: int | None,
    apifootball: ApiFootballConfig | None,
) -> ServeConfig:
    return ServeConfig(
        api_fixture_id=999001,
        interval_seconds=0.1,
        host="127.0.0.1",
        port=8000,
        database=DB,
        model=ModelConfig(provider="fake", anthropic=None),
        apifootball=apifootball,
        quota_floor=None,
        replay_path=replay_path,
        replay_step_minutes=replay_step,
        frontend_dir=None,
    )


def test_build_commentary_model_variants() -> None:
    fake = build_commentary_model(ModelConfig(provider="fake", anthropic=None))
    assert isinstance(fake, FakeModel)

    real = build_commentary_model(
        ModelConfig(
            provider="anthropic",
            anthropic=AnthropicConfig(api_key="sk-test", model="claude-opus-4-8", max_tokens=200),
        )
    )
    assert isinstance(real, AnthropicCommentaryModel)
    assert real.model_id == "claude-opus-4-8"

    with pytest.raises(ConfigError, match="anthropic"):
        build_commentary_model(ModelConfig(provider="anthropic", anthropic=None))


def test_build_serve_api_replay_and_live(tmp_path: Path) -> None:
    replay_path = tmp_path / "replay.json"
    ReplayFile(fixture=make_snapshot(), events=(make_event(),)).dump(replay_path)

    replay_api = build_serve_api(
        make_serve_config(replay_path=replay_path, replay_step=30, apifootball=None)
    )
    assert isinstance(replay_api, FakeFootballApi)

    with pytest.raises(ConfigError, match="replay step"):
        build_serve_api(
            make_serve_config(replay_path=replay_path, replay_step=None, apifootball=None)
        )

    live_api = build_serve_api(
        make_serve_config(
            replay_path=None,
            replay_step=None,
            apifootball=ApiFootballConfig(key="k", base_url="https://api.test"),
        )
    )
    assert isinstance(live_api, HttpxFootballApi)

    with pytest.raises(ConfigError, match="live mode"):
        build_serve_api(make_serve_config(replay_path=None, replay_step=None, apifootball=None))


def test_frontend_dir_ships_with_the_repo() -> None:
    assert FRONTEND_DIR.is_dir()
    assert (FRONTEND_DIR / "index.html").is_file()


async def test_serve_runtime_crash_cancels_siblings() -> None:
    server_started = asyncio.Event()
    server_cancelled = asyncio.Event()

    async def server() -> None:
        server_started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            server_cancelled.set()
            raise

    async def worker() -> None:
        await asyncio.sleep(30)

    async def ingestion() -> None:
        await server_started.wait()
        raise RuntimeError("ingestion died")

    with pytest.raises(ExceptionGroup) as excinfo:
        await serve_runtime(ingestion=ingestion(), worker=worker(), server=server())

    assert server_cancelled.is_set()
    assert "ingestion died" in str(excinfo.value.exceptions[0])


async def test_serve_runtime_completes_when_all_tasks_finish() -> None:
    async def quick() -> None:
        await asyncio.sleep(0)

    await serve_runtime(ingestion=quick(), worker=quick(), server=quick())


async def test_run_record_writes_replay_file(tmp_path: Path) -> None:
    api = StubFootballApi(
        snapshots=[make_snapshot(status=FixtureStatus.FULL_TIME, elapsed=90)],
        event_batches=[[make_event(), make_event(elapsed=80, detail="Penalty")]],
        remaining=99,
    )
    output = tmp_path / "recorded.json"
    replay = await run_record(api=api, api_fixture_id=1001, output=output)
    assert output.is_file()
    assert len(replay.events) == 2
    assert ReplayFile.load(output) == replay


async def test_run_record_rejects_unfinished_fixture(tmp_path: Path) -> None:
    api = StubFootballApi(
        snapshots=[make_snapshot(status=FixtureStatus.FIRST_HALF, elapsed=20)],
        event_batches=[[]],
        remaining=None,
    )
    with pytest.raises(RecordError, match="only finished fixtures"):
        await run_record(api=api, api_fixture_id=1001, output=tmp_path / "x.json")


async def test_run_status() -> None:
    expected = AccountStatus(
        account_name="Dan B", plan="Pro", active=True, requests_today=1, daily_limit=100
    )
    api = StubFootballApi(snapshots=[], event_batches=[], remaining=None, status=expected)
    assert await run_status(api=api) == expected
