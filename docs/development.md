# Development

## Layout

The importable package lives directly in `./api_football_cli/`.

```
api_football_cli/
  domain/             entities + pure logic: event hash, rendering, director, transcript, personas
  application/
    ports/            Protocols: FootballApi, repositories, EventBus, CommentaryModel
    services/         ingest_events, generate_commentary, stream_commentary, sync_reference
  adapters/
    inbound/web/      FastAPI routers, edge DTOs, hand-rolled SSE
    inbound/cli/      typer commands (afc)
    outbound/apifootball/   HttpxFootballApi + FakeFootballApi (replay)
    outbound/persistence/   SQLAlchemy 2.0 tables + repositories
    outbound/messaging/     PostgresListenNotifyBus + InMemoryBus
    outbound/model/         AnthropicCommentaryModel + FakeModel
  config.py           explicit env loading, no defaults
  main.py             composition root: wiring + TaskGroup supervision
alembic/              migrations (incl. the pg_notify triggers)
frontend/             no-build React chat UI
examples/             replay-demo.json (feeds tests, demos and the quickstart)
tests/                function-based pytest suite
```

The dependency rule points inward: domain and application import nothing from adapters;
adapters implement the application's ports; `main.py` wires everything.

## Checks (all must pass)

```bash
uv run ruff check
uv run ty check
uv run pytest --cov ./api_football_cli/ tests    # coverage gate: >90% (fail_under=90)
```

## Testing strategy

Everything runs with no network, no Postgres and no model spend:

- **Domain** — plain unit tests (hash identity, rendering, director, transcript merging).
- **HttpxFootballApi** — `httpx.MockTransport`; includes the critical errors-inside-HTTP-200
  contract and rate-limit header tracking.
- **Repositories** — real SQLAlchemy against in-memory SQLite (`sqlite+aiosqlite://`); the
  table types carry SQLite variants for exactly this purpose.
- **PostgresListenNotifyBus** — a fake asyncpg connection implementing the `NotifyConnection`
  protocol; trigger payloads are simulated by invoking the registered listener callback.
- **AnthropicCommentaryModel** — the real SDK over `MockTransport`, asserting role mapping,
  cache_control placement, usage extraction, refusal and error handling.
- **Services** — in-memory port fakes (`tests/fakes.py`). The in-memory event/commentary
  repositories publish a bus notification on insert, mirroring the Postgres AFTER INSERT
  triggers, so worker/stream tests exercise the same reactive flow as production.
- **Web** — REST via `httpx.ASGITransport`; SSE via a real uvicorn server on an ephemeral
  port, because ASGITransport buffers entire responses and can never observe an endless
  event stream.
- **CLI** — `typer.testing.CliRunner` with the composition functions monkeypatched; the
  `afc db upgrade` test runs the real Alembic migration against a SQLite file.
- **Pipeline** — `tests/test_pipeline.py` replays the shipped demo end-to-end:
  ingest → notifications → rounds → stream.

The only code not covered by the suite is `main.run_serve` itself (it needs a live Postgres);
it is a thin composition of individually tested builders, and was verified manually against a
disposable Postgres (`afc db upgrade` + replay serve + SSE curl).

## Deliberate implementation choices (vs. the architecture doc)

- **SSE is hand-rolled** (`adapters/inbound/web/sse.py`) instead of `sse-starlette`: ~40
  explicit lines, identical wire format (`id`/`event`/`data`, `retry`, comment heartbeats),
  no framework state, deterministic under test. The doc only *recommends* sse-starlette.
- **Terminal statuses** include the administrative endings (PST/CANC/ABD/AWD/WO) on top of
  the doc's {FT, AET, PEN} — polling those would never terminate either.
- **`afc record` and `afc status`** are small additions: record produces the replay files the
  doc's replay mode consumes; status is the doc's suggested preflight (§2).
- **Postgres ids** are `BIGINT GENERATED ALWAYS AS IDENTITY` via the migration; on SQLite
  (tests only) plain autoincrement integers.
- The api-football **base URL is a code constant** (`config.API_FOOTBALL_BASE_URL`), not an
  env var: it is a documented known value, and adapters take it explicitly for tests.

## Adding a model provider

Implement the `CommentaryModel` protocol (`application/ports/commentary_model.py`) in a new
module under `adapters/outbound/model/`, then register it in `config.load_model_config()` and
`main.build_commentary_model()`. The port speaks only neutral DTOs (`Turn`, `SpeakerRole`,
`CommentaryResult`); auth, role mapping and prompt caching are the adapter's business. The
adapter must keep the persona system prompt byte-stable per match — never interpolate the
score or the clock into it — so provider-side prompt caching keeps working.
