"""The afc CLI: argument validation, env handling, command wiring."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from api_football_cli import main as composition
from api_football_cli.adapters.inbound.cli.app import app
from api_football_cli.adapters.outbound.apifootball.fake import ReplayFile
from api_football_cli.application.services.sync_reference import (
    FixtureSyncReport,
    LeagueSyncReport,
    TeamSyncReport,
)
from api_football_cli.domain.entities import AccountStatus, FixtureStatus
from typer.testing import CliRunner

from tests.factories import make_event, make_snapshot
from tests.fakes import StubFootballApi

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]

SERVE_ARGS = [
    "serve",
    "--fixture",
    "999001",
    "--interval",
    "0.2",
    "--host",
    "127.0.0.1",
    "--port",
    "8200",
]


def set_base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFC_DATABASE_URL", "postgresql+asyncpg://app:pw@localhost/afc")
    monkeypatch.setenv("AFC_MODEL_PROVIDER", "fake")


def test_serve_replay_builds_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_base_env(monkeypatch)
    replay_path = tmp_path / "replay.json"
    ReplayFile(fixture=make_snapshot(), events=(make_event(),)).dump(replay_path)

    captured: dict[str, composition.ServeConfig] = {}

    async def fake_run_serve(config: composition.ServeConfig) -> None:
        captured["config"] = config

    monkeypatch.setattr(composition, "run_serve", fake_run_serve)
    result = runner.invoke(
        app, [*SERVE_ARGS, "--replay", str(replay_path), "--replay-step", "45"]
    )

    assert result.exit_code == 0, result.output
    config = captured["config"]
    assert config.api_fixture_id == 999001
    assert config.interval_seconds == 0.2
    assert config.replay_path == replay_path
    assert config.replay_step_minutes == 45
    assert config.apifootball is None
    assert config.model.provider == "fake"
    assert config.frontend_dir == composition.FRONTEND_DIR


def test_serve_live_requires_quota_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    set_base_env(monkeypatch)
    monkeypatch.setenv("AFC_APIFOOTBALL_KEY", "secret")
    result = runner.invoke(app, SERVE_ARGS)
    assert result.exit_code == 1
    assert "--quota-floor" in result.stderr


def test_serve_replay_requires_step(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_base_env(monkeypatch)
    result = runner.invoke(app, [*SERVE_ARGS, "--replay", str(tmp_path / "r.json")])
    assert result.exit_code == 1
    assert "--replay-step" in result.stderr


def test_serve_live_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    set_base_env(monkeypatch)
    monkeypatch.delenv("AFC_APIFOOTBALL_KEY", raising=False)
    result = runner.invoke(app, [*SERVE_ARGS, "--quota-floor", "5"])
    assert result.exit_code == 1
    assert "AFC_APIFOOTBALL_KEY" in result.stderr


def test_serve_live_builds_config(monkeypatch: pytest.MonkeyPatch) -> None:
    set_base_env(monkeypatch)
    monkeypatch.setenv("AFC_APIFOOTBALL_KEY", "secret")

    captured: dict[str, composition.ServeConfig] = {}

    async def fake_run_serve(config: composition.ServeConfig) -> None:
        captured["config"] = config

    monkeypatch.setattr(composition, "run_serve", fake_run_serve)
    result = runner.invoke(app, [*SERVE_ARGS, "--quota-floor", "10"])

    assert result.exit_code == 0, result.output
    config = captured["config"]
    assert config.apifootball is not None and config.apifootball.key == "secret"
    assert config.quota_floor == 10
    assert config.replay_path is None


def test_serve_surfaces_runtime_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_base_env(monkeypatch)
    replay_path = tmp_path / "replay.json"
    ReplayFile(fixture=make_snapshot(), events=()).dump(replay_path)

    async def exploding_run_serve(config: composition.ServeConfig) -> None:
        raise RuntimeError("runtime blew up")

    monkeypatch.setattr(composition, "run_serve", exploding_run_serve)
    result = runner.invoke(
        app, [*SERVE_ARGS, "--replay", str(replay_path), "--replay-step", "45"]
    )
    assert result.exit_code == 1
    assert "runtime blew up" in result.stderr


def test_record_writes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AFC_APIFOOTBALL_KEY", "secret")
    stub = StubFootballApi(
        snapshots=[make_snapshot(status=FixtureStatus.FULL_TIME, elapsed=90)],
        event_batches=[[make_event()]],
        remaining=None,
    )
    monkeypatch.setattr(composition, "build_live_api", lambda config: stub)

    output = tmp_path / "match.json"
    result = runner.invoke(app, ["record", "--fixture", "1001", "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert "1 events" in result.output
    assert ReplayFile.load(output).fixture.api_fixture_id == 1001


def test_record_rejects_unfinished(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AFC_APIFOOTBALL_KEY", "secret")
    stub = StubFootballApi(
        snapshots=[make_snapshot(status=FixtureStatus.FIRST_HALF, elapsed=12)],
        event_batches=[[]],
        remaining=None,
    )
    monkeypatch.setattr(composition, "build_live_api", lambda config: stub)
    result = runner.invoke(
        app, ["record", "--fixture", "1001", "--output", str(tmp_path / "m.json")]
    )
    assert result.exit_code == 1
    assert "only finished fixtures" in result.stderr


def test_record_requires_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AFC_APIFOOTBALL_KEY", raising=False)
    result = runner.invoke(
        app, ["record", "--fixture", "1001", "--output", str(tmp_path / "m.json")]
    )
    assert result.exit_code == 1
    assert "AFC_APIFOOTBALL_KEY" in result.stderr


def test_status_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFC_APIFOOTBALL_KEY", "secret")
    stub = StubFootballApi(
        snapshots=[],
        event_batches=[],
        remaining=None,
        status=AccountStatus(
            account_name="Dan B", plan="Pro", active=True, requests_today=7, daily_limit=7500
        ),
    )
    monkeypatch.setattr(composition, "build_live_api", lambda config: stub)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "plan='Pro'" in result.output
    assert "7/7500" in result.output


def test_sync_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFC_APIFOOTBALL_KEY", "secret")
    monkeypatch.setenv("AFC_DATABASE_URL", "postgresql+asyncpg://app:pw@localhost/afc")
    stub = StubFootballApi(snapshots=[], event_batches=[], remaining=None)
    monkeypatch.setattr(composition, "build_live_api", lambda config: stub)

    captured: dict[str, object] = {}

    async def fake_leagues(*, api: object, database_url: str, season: int) -> LeagueSyncReport:
        captured["leagues"] = (database_url, season)
        return LeagueSyncReport(leagues=3, seasons=6)

    async def fake_teams(
        *, api: object, database_url: str, league_api_id: int, season: int
    ) -> TeamSyncReport:
        captured["teams"] = (league_api_id, season)
        return TeamSyncReport(teams=20, venues=18)

    async def fake_fixtures(
        *, api: object, database_url: str, league_api_id: int, season: int
    ) -> FixtureSyncReport:
        captured["fixtures"] = (league_api_id, season)
        return FixtureSyncReport(fixtures=380)

    monkeypatch.setattr(composition, "run_sync_leagues", fake_leagues)
    monkeypatch.setattr(composition, "run_sync_teams", fake_teams)
    monkeypatch.setattr(composition, "run_sync_fixtures", fake_fixtures)

    result = runner.invoke(app, ["sync", "leagues", "--season", "2025"])
    assert result.exit_code == 0 and "3 leagues" in result.output
    assert captured["leagues"] == ("postgresql+asyncpg://app:pw@localhost/afc", 2025)

    result = runner.invoke(app, ["sync", "teams", "--league", "9990", "--season", "2025"])
    assert result.exit_code == 0 and "20 teams" in result.output
    assert captured["teams"] == (9990, 2025)

    result = runner.invoke(app, ["sync", "fixtures", "--league", "9990", "--season", "2025"])
    assert result.exit_code == 0 and "380 fixtures" in result.output
    assert captured["fixtures"] == (9990, 2025)


def test_sync_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFC_APIFOOTBALL_KEY", "secret")
    monkeypatch.delenv("AFC_DATABASE_URL", raising=False)
    result = runner.invoke(app, ["sync", "leagues", "--season", "2025"])
    assert result.exit_code == 1
    assert "AFC_DATABASE_URL" in result.stderr


def test_db_upgrade_applies_migrations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "migrated.db"
    monkeypatch.setenv("AFC_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    result = runner.invoke(app, ["db", "upgrade"])

    assert result.exit_code == 0, result.output + result.stderr
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in rows}
    finally:
        connection.close()
    assert {"fixture", "fixture_event", "commentary_message", "commentator"} <= tables


def test_db_upgrade_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.delenv("AFC_DATABASE_URL", raising=False)
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 1
    assert "AFC_DATABASE_URL" in result.stderr


def test_db_upgrade_requires_project_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AFC_DATABASE_URL", "sqlite+aiosqlite:///x.db")
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 1
    assert "project root" in result.stderr
