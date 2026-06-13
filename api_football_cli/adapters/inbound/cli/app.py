"""The afc command-line application (typer driving adapter).

Commands translate arguments + environment into use-case calls; all heavy
lifting lives in the composition root and the application services. Every
parameter is explicit — there are no default poll intervals, ports or steps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Annotated

import typer
from alembic.config import Config as AlembicConfig

from alembic import command as alembic_command
from api_football_cli import main as composition
from api_football_cli.config import (
    ConfigError,
    load_apifootball_config,
    load_database_config,
    load_model_config,
)

app = typer.Typer(
    name="afc",
    help="Live AI football commentators over api-football.",
    no_args_is_help=True,
)
sync_app = typer.Typer(help="One-shot reference-data crawls into Postgres.", no_args_is_help=True)
db_app = typer.Typer(help="Database schema management (Alembic).", no_args_is_help=True)
app.add_typer(sync_app, name="sync")
app.add_typer(db_app, name="db")

def _run[ResultT](coro: Coroutine[None, None, ResultT]) -> ResultT:
    try:
        return asyncio.run(coro)
    except (RuntimeError, FileNotFoundError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _config_error(message: str) -> typer.Exit:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


@app.command()
def serve(
    fixture: Annotated[int, typer.Option(help="api-football fixture id to follow.")],
    interval: Annotated[
        float, typer.Option(help="Seconds between polls (recommend 15-30 live).")
    ],
    host: Annotated[str, typer.Option(help="Bind address for the web server.")],
    port: Annotated[int, typer.Option(help="Bind port for the web server.")],
    replay: Annotated[
        Path | None,
        typer.Option(help="Replay file (from `afc record`); omit for live polling."),
    ] = None,
    replay_step: Annotated[
        int | None,
        typer.Option(help="Simulated match minutes advanced per poll (replay mode)."),
    ] = None,
    quota_floor: Annotated[
        int | None,
        typer.Option(help="Stop before the daily quota drops to this value (live mode)."),
    ] = None,
) -> None:
    """Run the live runtime: ingestion + commentary worker + web server."""
    try:
        if replay is not None:
            if replay_step is None:
                raise ConfigError("--replay requires --replay-step (minutes per poll)")
            apifootball = None
        else:
            if quota_floor is None:
                raise ConfigError("live mode requires --quota-floor")
            apifootball = load_apifootball_config()
        config = composition.ServeConfig(
            api_fixture_id=fixture,
            interval_seconds=interval,
            host=host,
            port=port,
            database=load_database_config(),
            model=load_model_config(),
            apifootball=apifootball,
            quota_floor=quota_floor,
            replay_path=replay,
            replay_step_minutes=replay_step,
            frontend_dir=composition.FRONTEND_DIR,
        )
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    _run(composition.run_serve(config))


@app.command()
def record(
    fixture: Annotated[int, typer.Option(help="api-football fixture id (must be finished).")],
    output: Annotated[Path, typer.Option(help="Path of the replay JSON to write.")],
) -> None:
    """Record a finished fixture into a replay file."""
    try:
        api = composition.build_live_api(load_apifootball_config())
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    replay = _run(composition.run_record(api=api, api_fixture_id=fixture, output=output))
    typer.echo(
        f"recorded fixture {fixture} ({replay.fixture.home.name} vs "
        f"{replay.fixture.away.name}, {len(replay.events)} events) -> {output}"
    )


@app.command()
def status() -> None:
    """Show api-football account status (does not consume quota)."""
    try:
        api = composition.build_live_api(load_apifootball_config())
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    account = _run(composition.run_status(api=api))
    typer.echo(
        f"account={account.account_name!r} plan={account.plan!r} active={account.active} "
        f"requests today={account.requests_today}/{account.daily_limit}"
    )


@sync_app.command("leagues")
def sync_leagues(
    season: Annotated[int, typer.Option(help="Season year, e.g. 2025.")],
) -> None:
    """Crawl leagues + seasons (+countries) for one season."""
    try:
        api = composition.build_live_api(load_apifootball_config())
        database = load_database_config()
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    report = _run(
        composition.run_sync_leagues(api=api, database_url=database.url, season=season)
    )
    typer.echo(f"synced {report.leagues} leagues, {report.seasons} seasons")


@sync_app.command("teams")
def sync_teams(
    league: Annotated[int, typer.Option(help="api-football league id.")],
    season: Annotated[int, typer.Option(help="Season year, e.g. 2025.")],
) -> None:
    """Crawl teams + venues for one league season."""
    try:
        api = composition.build_live_api(load_apifootball_config())
        database = load_database_config()
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    report = _run(
        composition.run_sync_teams(
            api=api, database_url=database.url, league_api_id=league, season=season
        )
    )
    typer.echo(f"synced {report.teams} teams, {report.venues} venues")


@sync_app.command("fixtures")
def sync_fixtures(
    league: Annotated[int, typer.Option(help="api-football league id.")],
    season: Annotated[int, typer.Option(help="Season year, e.g. 2025.")],
) -> None:
    """Crawl fixtures for one league season."""
    try:
        api = composition.build_live_api(load_apifootball_config())
        database = load_database_config()
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    report = _run(
        composition.run_sync_fixtures(
            api=api, database_url=database.url, league_api_id=league, season=season
        )
    )
    typer.echo(f"synced {report.fixtures} fixtures")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply Alembic migrations up to head (requires AFC_DATABASE_URL)."""
    ini_path = Path("alembic.ini")
    if not ini_path.is_file():
        raise _config_error(
            "alembic.ini not found; run `afc db upgrade` from the project root"
        )
    try:
        load_database_config()  # fail fast with a clear message before alembic runs
        alembic_command.upgrade(AlembicConfig(str(ini_path)), "head")
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    typer.echo("database schema is up to date")


def main() -> None:
    app()
