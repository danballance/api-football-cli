# Usage

## Configuration

All configuration is explicit. Missing values fail fast with the exact variable name.

| Variable | Required by | Meaning |
|---|---|---|
| `AFC_DATABASE_URL` | serve, sync, db | SQLAlchemy async URL. The live runtime requires `postgresql+asyncpg://…` (LISTEN/NOTIFY needs Postgres). |
| `AFC_APIFOOTBALL_KEY` | live serve, record, status, sync | Your api-football.com key (`x-apisports-key`). |
| `AFC_MODEL_PROVIDER` | serve | `anthropic` or `fake`. There is no default provider. |
| `AFC_ANTHROPIC_API_KEY` | serve (anthropic) | Anthropic API key. |
| `AFC_ANTHROPIC_MODEL` | serve (anthropic) | Model id, e.g. `claude-opus-4-8`. |
| `AFC_ANTHROPIC_MAX_TOKENS` | serve (anthropic) | Per-line output cap, e.g. `300`. |

CLI parameters (interval, ports, replay step, quota floor) are likewise explicit — there are
no default values.

## Database setup

```bash
export AFC_DATABASE_URL="postgresql+asyncpg://user:password@localhost:5432/afc"
uv run afc db upgrade        # alembic upgrade head, must run from the project root
```

The initial migration creates the schema plus two `AFTER INSERT` triggers:

- `fixture_event` insert → `pg_notify('fixture_event_inserted', '{"fixture_id": …, "id": …}')`
- `commentary_message` insert → `pg_notify('commentary_inserted', …)`

These notifications are the reactive spine: the commentary worker and the SSE stream are woken
by them; payloads carry row ids only and listeners catch up with a `SELECT … WHERE id > last`.

## `afc serve` — the live runtime

One async process supervising three tasks in a single `asyncio.TaskGroup`: ingestion, the
commentary worker, and the FastAPI/SSE server. If any task fails the process exits (fail
fast); the database is the durable source of truth, so a supervisor can simply restart it.

```bash
# Replay (dev/demo default): no API key needed
uv run afc serve --fixture 999001 --interval 0.5 \
  --replay examples/replay-demo.json --replay-step 5 \
  --host 127.0.0.1 --port 8000

# Live: poll api-football every 20s, stop before the daily quota hits 10
uv run afc serve --fixture 1145509 --interval 20 --quota-floor 10 \
  --host 127.0.0.1 --port 8000
```

- `--interval` — seconds between polls. api-football updates live data every ~15s and
  recommends ~1 request/minute per fixture; 15–30s is sensible. Required, no default.
- `--replay` / `--replay-step` — replay file and how many simulated match minutes each poll
  advances. `--replay-step 5` with `--interval 0.5` plays 90 minutes in ~9 seconds of polls.
- `--quota-floor` — live mode only: ingestion fails fast when the daily quota (taken from the
  rate-limit headers on every response) drops to this value.

The serve process keeps serving the UI after full time; stop it with Ctrl+C.

### Web API

| Endpoint | Description |
|---|---|
| `GET /fixtures` | Fixtures in the database |
| `GET /fixtures/{id}` | Scoreboard: status, elapsed, score |
| `GET /fixtures/{id}/events` | The append-only event log, rendered |
| `GET /fixtures/{id}/commentary?after_id=0` | Commentary history |
| `GET /fixtures/{id}/commentary/stream` | SSE: catch-up replay then live push |
| `GET /commentators` | The two booth personas |
| `GET /` | The React chat UI |

The SSE stream sends whole messages (`event: commentary`, `id: <message id>`); browsers
reconnect automatically and replay from `Last-Event-ID`. `{id}` is **our** fixture id (from
`GET /fixtures`), not the api-football id.

## `afc record` — capture a replay

```bash
uv run afc record --fixture 1035043 --output my-match.json
```

Fetches a **finished** fixture (status FT/AET/PEN…) and writes its metadata plus the full
event list. Costs two API requests. The file feeds `afc serve --replay`.

## `afc status`

Prints account, plan and today's request usage. Uses `/status`, which does not count against
the daily quota — a safe preflight check.

## `afc sync` — reference data

One-shot crawls, upserted on the `api_*_id` columns (idempotent, safe to re-run):

```bash
uv run afc sync leagues  --season 2025                 # leagues + seasons + countries
uv run afc sync teams    --league 39 --season 2025     # teams + venues
uv run afc sync fixtures --league 39 --season 2025     # fixture rows
```

Live ingestion creates minimal league/team rows on its own; sync enriches them.

## The frontend

`frontend/` is a no-build React app (ES modules via an import map; React + htm from esm.sh)
served as static files by FastAPI on the same origin. It picks the first fixture from
`/fixtures` (override with `/?fixture=<id>`), opens the SSE stream, and polls the scoreboard
every 10 seconds. Editing `frontend/*.js|css|html` requires only a browser refresh.
