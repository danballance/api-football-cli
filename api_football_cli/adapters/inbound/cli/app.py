"""The afc command-line application (typer driving adapter).

Commands translate arguments + environment into use-case calls; all heavy
lifting lives in the composition root and the application services. Every
operational parameter is explicit.
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
from api_football_cli.application.ports.football_api import FootballApi
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


async def _run_and_close_api[ResultT](
    *, api: FootballApi, coro: Coroutine[None, None, ResultT]
) -> ResultT:
    try:
        return await coro
    finally:
        await composition.close_football_api(api)


@app.command()
def web(
    host: Annotated[str, typer.Option(help="Bind address for the web server.")],
    port: Annotated[int, typer.Option(help="Bind port for the web server.")],
    sse_ping_seconds: Annotated[
        float, typer.Option(help="Seconds between SSE keep-alive comments.")
    ],
) -> None:
    """Run FastAPI/Uvicorn only."""
    try:
        config = composition.WebConfig(
            host=host,
            port=port,
            database=load_database_config(),
            frontend_dir=composition.FRONTEND_DIR,
            sse_ping_seconds=sse_ping_seconds,
        )
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    _run(composition.run_web(config))


@app.command()
def ingest(
    fixture: Annotated[int, typer.Option(help="api-football fixture id to follow.")],
    interval: Annotated[
        float, typer.Option(help="Seconds between polls (recommend 15-30 live).")
    ],
    quota_floor: Annotated[
        int, typer.Option(help="Stop before the daily quota drops to this value.")
    ],
) -> None:
    """Poll live fixture data into Postgres."""
    try:
        config = composition.IngestConfig(
            api_fixture_id=fixture,
            interval_seconds=interval,
            database=load_database_config(),
            apifootball=load_apifootball_config(),
            quota_floor=quota_floor,
        )
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    final = _run(composition.run_ingest(config))
    typer.echo(
        f"ingested fixture {final.api_fixture_id} through status={final.status.value!r}"
    )


@app.command()
def worker(
    fixture: Annotated[int, typer.Option(help="api-football fixture id to commentate.")],
    fixture_wait_seconds: Annotated[
        float,
        typer.Option(
            help="Seconds to wait for the ingester to create the fixture row; use 0 to fail fast."
        ),
    ],
    max_messages_per_round: Annotated[
        int, typer.Option(help="Maximum commentary messages produced per event round.")
    ],
) -> None:
    """Generate commentary from stored fixture events."""
    try:
        config = composition.WorkerConfig(
            api_fixture_id=fixture,
            database=load_database_config(),
            model=load_model_config(),
            fixture_wait_seconds=fixture_wait_seconds,
            max_messages_per_round=max_messages_per_round,
        )
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    _run(composition.run_worker(config))


@app.command()
def dev(
    fixture: Annotated[int, typer.Option(help="api-football fixture id to follow.")],
    interval: Annotated[
        float, typer.Option(help="Seconds between polls (recommend 15-30 live).")
    ],
    host: Annotated[str, typer.Option(help="Bind address for the web server.")],
    port: Annotated[int, typer.Option(help="Bind port for the web server.")],
    sse_ping_seconds: Annotated[
        float, typer.Option(help="Seconds between SSE keep-alive comments.")
    ],
    max_messages_per_round: Annotated[
        int, typer.Option(help="Maximum commentary messages produced per event round.")
    ],
    quota_floor: Annotated[
        int, typer.Option(help="Stop before the daily quota drops to this value.")
    ],
) -> None:
    """Run ingestion + commentary worker + web server in one local dev process."""
    try:
        config = composition.DevConfig(
            api_fixture_id=fixture,
            interval_seconds=interval,
            host=host,
            port=port,
            database=load_database_config(),
            model=load_model_config(),
            apifootball=load_apifootball_config(),
            quota_floor=quota_floor,
            frontend_dir=composition.FRONTEND_DIR,
            sse_ping_seconds=sse_ping_seconds,
            max_messages_per_round=max_messages_per_round,
        )
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    _run(composition.run_dev(config))


@app.command()
def status() -> None:
    """Show api-football account status (does not consume quota)."""
    try:
        api = composition.build_live_api(load_apifootball_config())
    except ConfigError as exc:
        raise _config_error(str(exc)) from exc
    account = _run(
        _run_and_close_api(api=api, coro=composition.run_status(api=api))
    )
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
        _run_and_close_api(
            api=api,
            coro=composition.run_sync_leagues(
                api=api, database_url=database.url, season=season
            ),
        )
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
        _run_and_close_api(
            api=api,
            coro=composition.run_sync_teams(
                api=api,
                database_url=database.url,
                league_api_id=league,
                season=season,
            ),
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
        _run_and_close_api(
            api=api,
            coro=composition.run_sync_fixtures(
                api=api,
                database_url=database.url,
                league_api_id=league,
                season=season,
            ),
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
