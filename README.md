# Live AI Football Commentators

Two AI commentators — **Marty Vox** (play-by-play) and **Robbie Banks** (colour) — watch a
football match from api-football and talk to each other about it in real time. Commentary is
pushed to a React chat UI over Server-Sent Events.

```
api-football  →  Ingestion  →  Postgres  →  Commentary worker  →  Postgres  →  FastAPI (SSE)  →  React
  (HTTP poll)    (async task)   (events)    (2× model calls)      (messages)    (push)          (chat UI)
```

The whole runtime is event-driven: Postgres `LISTEN/NOTIFY` is the spine — inserting a match
event wakes the commentary worker, inserting a commentary line wakes the SSE stream. The full
design lives in [.tasks/architecture.md](.tasks/architecture.md).

## Quickstart (replay mode — no API key, no model spend)

Replay mode replays a recorded fixture on an accelerated clock through the exact same pipeline
as a live match.

```bash
uv sync

# Postgres is required (LISTEN/NOTIFY). Point at any empty database:
export AFC_DATABASE_URL="postgresql+asyncpg://user:password@localhost:5432/afc"
export AFC_MODEL_PROVIDER=fake     # canned lines, no model calls

uv run afc db upgrade              # create schema + notify triggers
uv run afc serve \
  --fixture 999001 \
  --interval 0.5 \
  --replay examples/replay-demo.json \
  --replay-step 5 \
  --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> — the demo match plays out in a couple of minutes with live
commentary, scoreboard and a red card.

To use real AI commentators, switch the provider:

```bash
export AFC_MODEL_PROVIDER=anthropic
export AFC_ANTHROPIC_API_KEY=sk-ant-...
export AFC_ANTHROPIC_MODEL=claude-opus-4-8
export AFC_ANTHROPIC_MAX_TOKENS=300
```

## Live mode

```bash
export AFC_APIFOOTBALL_KEY=...     # api-football.com key
uv run afc status                  # check your plan/quota (free, does not consume quota)
uv run afc serve --fixture <id> --interval 20 --quota-floor 10 --host 127.0.0.1 --port 8000
```

Record a finished fixture for later replays:

```bash
uv run afc record --fixture <id> --output my-match.json
```

## Reference data

One-shot crawls into Postgres (outside the live runtime):

```bash
uv run afc sync leagues  --season 2025
uv run afc sync teams    --league 39 --season 2025
uv run afc sync fixtures --league 39 --season 2025
```

## Documentation

- [docs/usage.md](docs/usage.md) — commands, configuration, replay/record workflow
- [docs/development.md](docs/development.md) — layout, testing strategy, checks
- [.tasks/architecture.md](.tasks/architecture.md) — the full architecture design

## Checks

```bash
uv run ruff check
uv run ty check
uv run pytest --cov ./api-football-cli/ tests
```
