# Usage

## Configuration

All configuration is explicit. Missing values fail fast with the exact variable name.

| Variable | Required by | Meaning |
|---|---|---|
| `AFC_DATABASE_URL` | web, ingest, worker, dev, sync, db | SQLAlchemy async Postgres URL. Runtime processes require `postgresql+asyncpg://…`. |
| `AFC_APIFOOTBALL_KEY` | ingest, dev, status, sync | Your api-football.com key (`x-apisports-key`). |
| `AFC_MODEL_PROVIDER` | worker, dev | `anthropic` or `fake`. There is no default provider. |
| `AFC_ANTHROPIC_API_KEY` | worker/dev with anthropic | Anthropic API key. |
| `AFC_ANTHROPIC_MODEL` | worker/dev with anthropic | Model id, e.g. `claude-opus-4-8`. |
| `AFC_ANTHROPIC_MAX_TOKENS` | worker/dev with anthropic | Per-line output cap, e.g. `300`. |

CLI parameters such as poll intervals, ports, worker wait, SSE ping, and quota floor are
explicit.

## Database setup

```bash
export AFC_DATABASE_URL="postgresql+asyncpg://user:password@localhost:5432/afc"
uv run afc db upgrade        # alembic upgrade head, must run from the project root
```

The initial migration creates the schema plus two `AFTER INSERT` triggers:

- `fixture_event` insert -> `pg_notify('fixture_event_inserted', '{"fixture_id": ..., "id": ...}')`
- `commentary_message` insert -> `pg_notify('commentary_inserted', ...)`

These notifications are the reactive spine: the commentary worker and the SSE stream are woken
by them; payloads carry row ids only and listeners catch up with a `SELECT ... WHERE id > last`.

## Production Process Commands

Run one concern per process:

```bash
uv run afc web \
  --host 0.0.0.0 \
  --port 8000 \
  --sse-ping-seconds 15

uv run afc ingest \
  --fixture 1145509 \
  --interval 20 \
  --quota-floor 10

uv run afc worker \
  --fixture 1145509 \
  --fixture-wait-seconds 60 \
  --max-messages-per-round 2
```

`afc web` serves REST, SSE, and the React UI only. It needs the database and listens for
`commentary_inserted`.

`afc ingest` polls api-football and writes fixture/events/request-log rows. It exits when the
fixture reaches a terminal status.

`afc worker` resolves the internal fixture row from the api-football fixture id, takes a
Postgres advisory lock for that fixture, listens for `fixture_event_inserted`, and writes
commentary. `--fixture-wait-seconds 0` fails immediately if ingestion has not prepared the
fixture row.

## `afc dev` — Local All-In-One Runtime

`afc dev` keeps the single-process TaskGroup composition for local development:

```bash
uv run afc dev \
  --fixture 1145509 \
  --interval 20 \
  --quota-floor 10 \
  --host 127.0.0.1 \
  --port 8000 \
  --sse-ping-seconds 15 \
  --max-messages-per-round 2
```

The dev process keeps serving the UI after full time; stop it with Ctrl+C.

### Web API

| Endpoint | Description |
|---|---|
| `GET /fixtures` | Fixtures in the database |
| `GET /fixtures/{id}` | Scoreboard: status, elapsed, score |
| `GET /fixtures/{id}/events` | The append-only event log, rendered |
| `GET /fixtures/{id}/commentary?after_id=0` | Commentary history |
| `GET /fixtures/{id}/commentary/stream` | SSE: catch-up then live push |
| `GET /commentators` | The two booth personas |
| `GET /` | The React chat UI |

The SSE stream sends whole messages (`event: commentary`, `id: <message id>`); browsers
reconnect automatically from `Last-Event-ID`. `{id}` is **our** fixture id from `GET /fixtures`,
not the api-football id.

## `afc status`

Prints account, plan and today's request usage. Uses `/status`, which does not count against
the daily quota.

## `afc sync` — Reference Data

One-shot crawls, upserted on the `api_*_id` columns:

```bash
uv run afc sync leagues  --season 2025
uv run afc sync teams    --league 39 --season 2025
uv run afc sync fixtures --league 39 --season 2025
```

Live ingestion creates minimal league/team rows on its own; sync enriches them.

## The Frontend

`frontend/` is a no-build React app served as static files by FastAPI on the same origin. It
picks the first fixture from `/fixtures` (override with `/?fixture=<id>`), opens the SSE stream,
and polls the scoreboard every 10 seconds.
