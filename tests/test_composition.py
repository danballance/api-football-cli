"""Composition root: builders, status flow, TaskGroup supervision."""

from __future__ import annotations

import asyncio

import pytest

from api_football_cli.adapters.outbound.apifootball.http import HttpxFootballApi
from api_football_cli.adapters.outbound.model.anthropic import AnthropicCommentaryModel
from api_football_cli.adapters.outbound.model.fake import FakeModel
from api_football_cli.config import AnthropicConfig, ApiFootballConfig, ConfigError, ModelConfig
from api_football_cli.domain.entities import AccountStatus
from api_football_cli.main import (
    FRONTEND_DIR,
    _wait_for_fixture,
    build_commentary_model,
    build_live_api,
    run_status,
    serve_runtime,
)
from tests.factories import make_snapshot
from tests.fakes import InMemoryFixtureRepository, StubFootballApi


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


async def test_build_live_api() -> None:
    api = build_live_api(ApiFootballConfig(key="k", base_url="https://api.test"))
    assert isinstance(api, HttpxFootballApi)
    await api.aclose()


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


async def test_wait_for_fixture_fails_when_not_prepared() -> None:
    fixtures = InMemoryFixtureRepository()
    with pytest.raises(RuntimeError, match="not prepared"):
        await _wait_for_fixture(fixtures=fixtures, api_fixture_id=1001, wait_seconds=0)


async def test_wait_for_fixture_returns_prepared_row() -> None:
    fixtures = InMemoryFixtureRepository()
    created = await fixtures.upsert_snapshot(make_snapshot(api_fixture_id=1001))
    found = await _wait_for_fixture(fixtures=fixtures, api_fixture_id=1001, wait_seconds=0)
    assert found == created


async def test_run_status() -> None:
    expected = AccountStatus(
        account_name="Dan B", plan="Pro", active=True, requests_today=1, daily_limit=100
    )
    api = StubFootballApi(snapshots=[], event_batches=[], remaining=99, status=expected)
    assert await run_status(api=api) == expected
