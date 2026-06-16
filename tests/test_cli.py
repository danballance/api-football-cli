"""The afc CLI: argument validation, env handling, command wiring."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from api_football_cli import main as composition
from api_football_cli.adapters.inbound.cli.app import app
from api_football_cli.adapters.outbound.apifootball.fake import ReplayFile
from api_football_cli.application.services.sync_reference import (
    FixtureSyncReport,
    LeagueSyncReport,
    TeamSyncReport,
)
from api_football_cli.domain.entities import (
    AccountStatus,
    Fixture,
    FixtureStatus,
    StoredTeam,
)
from tests.factories import make_event, make_snapshot
from tests.fakes import StubFootballApi

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]

DEV_ARGS = [
    "dev",
    "--fixture",
    "999001",
    "--interval",
    "0.2",
    "--host",
    "127.0.0.1",
    "--port",
    "8200",
    "--sse-ping-seconds",
    "15",
    "--max-messages-per-round",
    "2",
]

INGEST_ARGS = [
    "ingest",
    "--fixture",
    "999001",
    "--interval",
    "0.2",
]


def set_base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFC_DATABASE_URL", "postgresql+asyncpg://app:pw@localhost/afc")
    monkeypatch.setenv("AFC_MODEL_PROVIDER", "fake")


def make_fixture() -> Fixture:
    snapshot = make_snapshot(api_fixture_id=999001, status=FixtureStatus.FULL_TIME, elapsed=90)
    return Fixture(
        id=1,
        api_fixture_id=snapshot.api_fixture_id,
        league=snapshot.league,
        kickoff=snapshot.kickoff,
        status=snapshot.status,
        elapsed=snapshot.elapsed,
        home=StoredTeam(
            id=snapshot.home.api_team_id,
            api_team_id=snapshot.home.api_team_id,
            name=snapshot.home.name,
        ),
        away=StoredTeam(
            id=snapshot.away.api_team_id,
            api_team_id=snapshot.away.api_team_id,
            name=snapshot.away.name,
        ),
        home_goals=snapshot.home_goals,
        away_goals=snapshot.away_goals,
        referee=snapshot.referee,
    )


def test_dev_replay_builds_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_base_env(monkeypatch)
    replay_path = tmp_path / "replay.json"
    ReplayFile(fixture=make_snapshot(), events=(make_event(),)).dump(replay_path)

    captured: dict[str, composition.DevConfig] = {}

    async def fake_run_dev(config: composition.DevConfig) -> None:
        captured["config"] = config

    monkeypatch.setattr(composition, "run_dev", fake_run_dev)
    result = runner.invoke(
        app, [*DEV_ARGS, "--replay", str(replay_path), "--replay-step", "45"]
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
    assert config.sse_ping_seconds == 15
    assert config.max_messages_per_round == 2


def test_dev_live_requires_quota_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    set_base_env(monkeypatch)
    monkeypatch.setenv("AFC_APIFOOTBALL_KEY", "secret")
    result = runner.invoke(app, DEV_ARGS)
    assert result.exit_code == 1
    assert "--quota-floor" in result.stderr


def test_dev_replay_requires_step(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_base_env(monkeypatch)
    result = runner.invoke(app, [*DEV_ARGS, "--replay", str(tmp_path / "r.json")])
    assert result.exit_code == 1
    assert "--replay-step" in result.stderr


def test_dev_live_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    set_base_env(monkeypatch)
    monkeypatch.delenv("AFC_APIFOOTBALL_KEY", raising=False)
    result = runner.invoke(app, [*DEV_ARGS, "--quota-floor", "5"])
    assert result.exit_code == 1
    assert "AFC_APIFOOTBALL_KEY" in result.stderr


def test_dev_live_builds_config(monkeypatch: pytest.MonkeyPatch) -> None:
    set_base_env(monkeypatch)
    monkeypatch.setenv("AFC_APIFOOTBALL_KEY", "secret")

    captured: dict[str, composition.DevConfig] = {}

    async def fake_run_dev(config: composition.DevConfig) -> None:
        captured["config"] = config

    monkeypatch.setattr(composition, "run_dev", fake_run_dev)
    result = runner.invoke(app, [*DEV_ARGS, "--quota-floor", "10"])

    assert result.exit_code == 0, result.output
    config = captured["config"]
    assert config.apifootball is not None and config.apifootball.key == "secret"
    assert config.quota_floor == 10
    assert config.replay_path is None


def test_dev_surfaces_runtime_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_base_env(monkeypatch)
    replay_path = tmp_path / "replay.json"
    ReplayFile(fixture=make_snapshot(), events=()).dump(replay_path)

    async def exploding_run_dev(config: composition.DevConfig) -> None:
        raise RuntimeError("runtime blew up")

    monkeypatch.setattr(composition, "run_dev", exploding_run_dev)
    result = runner.invoke(
        app, [*DEV_ARGS, "--replay", str(replay_path), "--replay-step", "45"]
    )
    assert result.exit_code == 1
    assert "runtime blew up" in result.stderr


def test_web_builds_config(monkeypatch: pytest.MonkeyPatch) -> None:
    set_base_env(monkeypatch)
    captured: dict[str, composition.WebConfig] = {}

    async def fake_run_web(config: composition.WebConfig) -> None:
        captured["config"] = config

    monkeypatch.setattr(composition, "run_web", fake_run_web)
    result = runner.invoke(
        app,
        [
            "web",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--sse-ping-seconds",
            "12.5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["config"].host == "0.0.0.0"
    assert captured["config"].port == 8000
    assert captured["config"].sse_ping_seconds == 12.5


def test_ingest_replay_builds_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_base_env(monkeypatch)
    replay_path = tmp_path / "replay.json"
    ReplayFile(fixture=make_snapshot(), events=(make_event(),)).dump(replay_path)
    captured: dict[str, composition.IngestConfig] = {}

    async def fake_run_ingest(config: composition.IngestConfig) -> Fixture:
        captured["config"] = config
        return make_fixture()

    monkeypatch.setattr(composition, "run_ingest", fake_run_ingest)
    result = runner.invoke(
        app, [*INGEST_ARGS, "--replay", str(replay_path), "--replay-step", "20"]
    )

    assert result.exit_code == 0, result.output
    assert "status='FT'" in result.output
    assert captured["config"].replay_path == replay_path
    assert captured["config"].replay_step_minutes == 20
    assert captured["config"].apifootball is None


def test_ingest_live_builds_config(monkeypatch: pytest.MonkeyPatch) -> None:
    set_base_env(monkeypatch)
    monkeypatch.setenv("AFC_APIFOOTBALL_KEY", "secret")
    captured: dict[str, composition.IngestConfig] = {}

    async def fake_run_ingest(config: composition.IngestConfig) -> Fixture:
        captured["config"] = config
        return make_fixture()

    monkeypatch.setattr(composition, "run_ingest", fake_run_ingest)
    result = runner.invoke(app, [*INGEST_ARGS, "--quota-floor", "10"])

    assert result.exit_code == 0, result.output
    assert captured["config"].apifootball is not None
    assert captured["config"].quota_floor == 10


def test_worker_builds_config(monkeypatch: pytest.MonkeyPatch) -> None:
    set_base_env(monkeypatch)
    captured: dict[str, composition.WorkerConfig] = {}

    async def fake_run_worker(config: composition.WorkerConfig) -> None:
        captured["config"] = config

    monkeypatch.setattr(composition, "run_worker", fake_run_worker)
    result = runner.invoke(
        app,
        [
            "worker",
            "--fixture",
            "999001",
            "--fixture-wait-seconds",
            "30",
            "--max-messages-per-round",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["config"].api_fixture_id == 999001
    assert captured["config"].fixture_wait_seconds == 30
    assert captured["config"].max_messages_per_round == 2


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


def test_db_upgrade_requires_project_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AFC_DATABASE_URL", "sqlite+aiosqlite:///x.db")
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 1
    assert "project root" in result.stderr
