# Live AI Football Commentators

Two AI commentators — **Marty Vox** (play-by-play) and **Robbie Banks** (colour) — watch a
football match from api-football and talk to each other about it in real time. Commentary is
pushed to a React chat UI over Server-Sent Events.

```
api-football  ->  ingester  ->  Postgres  ->  commentary-worker  ->  Postgres  ->  web  ->  React
  (HTTP poll)      (process)     (events)      (model calls)          (messages)    (SSE)    (chat UI)
```

The whole runtime is event-driven: Postgres `LISTEN/NOTIFY` is the spine — inserting a match
event wakes the commentary worker, inserting a commentary line wakes the SSE stream. The full
design lives in [.tasks/architecture.md](.tasks/architecture.md).

## Quickstart

```bash
uv sync

# Postgres is required (LISTEN/NOTIFY). Point at any empty database:
export AFC_DATABASE_URL="postgresql+asyncpg://user:password@localhost:5432/afc"
export AFC_APIFOOTBALL_KEY=...     # api-football.com key
export AFC_MODEL_PROVIDER=fake     # canned lines, no model calls

uv run afc db upgrade              # create schema + notify triggers
uv run afc status                  # check your plan/quota
uv run afc dev \
  --fixture <id> \
  --interval 20 \
  --host 127.0.0.1 --port 8000 \
  --sse-ping-seconds 15 \
  --max-messages-per-round 2
```

Open <http://127.0.0.1:8000> to watch the live fixture with commentary and scoreboard updates.

To use real AI commentators, switch the provider:

```bash
export AFC_MODEL_PROVIDER=anthropic
export AFC_ANTHROPIC_API_KEY=sk-ant-...
export AFC_ANTHROPIC_MODEL=claude-opus-4-8
export AFC_ANTHROPIC_MAX_TOKENS=300
```

## Split runtime

```bash
# Run web and ingest in separate terminals/processes first:
uv run afc web --host 127.0.0.1 --port 8000 --sse-ping-seconds 15
uv run afc ingest --fixture <id> --interval 20

# After ingestion has created the fixture row, start the worker:
uv run afc worker --fixture <id> --max-messages-per-round 2
```

## Reference data

One-shot crawls into Postgres (outside the live runtime):

```bash
uv run afc sync leagues  --season 2025
uv run afc sync teams    --league 39 --season 2025
uv run afc sync fixtures --league 39 --season 2025
```

## Documentation

- [docs/usage.md](docs/usage.md) — commands and configuration
- [docs/development.md](docs/development.md) — layout, testing strategy, checks
- [.tasks/architecture.md](.tasks/architecture.md) — the full architecture design

## Checks

```bash
uv run ruff check
uv run ty check
uv run pytest --cov ./api_football_cli/ tests
```
