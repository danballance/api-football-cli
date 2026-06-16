"""Web adapter: REST endpoints and the SSE commentary stream.

REST endpoints are exercised over httpx's ASGITransport. The SSE endpoint is
exercised over a real uvicorn server on an ephemeral port: ASGITransport
buffers the whole response, so it can never observe an endless event stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from api_football_cli.adapters.inbound.web.app import WebDeps, create_app
from api_football_cli.adapters.inbound.web.sse import commentary_sse, format_sse
from api_football_cli.adapters.outbound.messaging.in_memory import InMemoryBus
from api_football_cli.application.services.stream_commentary import StreamCommentary
from api_football_cli.domain.entities import CommentaryDraft, CommentaryMessage
from api_football_cli.domain.personas import PERSONAS
from api_football_cli.main import FRONTEND_DIR
from tests.factories import make_event, make_snapshot
from tests.fakes import (
    InMemoryCommentaryRepository,
    InMemoryCommentatorRepository,
    InMemoryEventRepository,
    InMemoryFixtureRepository,
    next_timestamp,
)


class Harness:
    def __init__(
        self,
        *,
        bus: InMemoryBus,
        fixtures: InMemoryFixtureRepository,
        events: InMemoryEventRepository,
        commentary: InMemoryCommentaryRepository,
        fixture_id: int,
        app: FastAPI,
    ) -> None:
        self.bus = bus
        self.fixtures = fixtures
        self.events = events
        self.commentary = commentary
        self.fixture_id = fixture_id
        self.app = app

    def asgi_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )


async def build_harness() -> Harness:
    bus = InMemoryBus()
    fixtures = InMemoryFixtureRepository()
    events = InMemoryEventRepository(bus=bus)
    commentary = InMemoryCommentaryRepository(bus=bus)
    commentators = InMemoryCommentatorRepository()
    for persona in PERSONAS:
        await commentators.upsert(persona)
    fixture = await fixtures.upsert_snapshot(make_snapshot())
    await events.insert_if_new(fixture_id=fixture.id, event=make_event())

    deps = WebDeps(
        fixtures=fixtures,
        events=events,
        commentary=commentary,
        commentators=commentators,
        stream=StreamCommentary(commentary=commentary, bus=bus),
        sse_ping_seconds=0.05,
    )
    app = create_app(deps=deps, frontend_dir=None)
    return Harness(
        bus=bus,
        fixtures=fixtures,
        events=events,
        commentary=commentary,
        fixture_id=fixture.id,
        app=app,
    )

def draft(fixture_id: int, text: str) -> CommentaryDraft:
    return CommentaryDraft(
        fixture_id=fixture_id,
        commentator_id=1,
        text=text,
        triggering_event_id=None,
        in_reply_to=None,
        provider="fake",
        model="fake-1",
        usage=None,
    )


async def test_rest_endpoints() -> None:
    harness = await build_harness()
    await harness.commentary.insert(draft(harness.fixture_id, "Hello booth"))
    async with harness.asgi_client() as client:
        fixtures = (await client.get("/fixtures")).json()
        assert len(fixtures) == 1
        assert fixtures[0]["home"]["name"] == "Riverton Albion"
        assert fixtures[0]["away"]["name"] == "Kingsport Wanderers"
        assert fixtures[0]["league"] == "Demo Premier League"
        assert "status" not in fixtures[0]
        assert "home_goals" not in fixtures[0]

        events = (await client.get(f"/fixtures/{harness.fixture_id}/events")).json()
        assert len(events) == 1
        assert events[0]["minute"] == "10'"
        assert "GOAL" in events[0]["text"]

        commentators = (await client.get("/commentators")).json()
        assert [c["role"] for c in commentators] == ["play_by_play", "color"]

        commentary = (
            await client.get(f"/fixtures/{harness.fixture_id}/commentary")
        ).json()
        assert [m["text"] for m in commentary] == ["Hello booth"]

        after = (
            await client.get(
                f"/fixtures/{harness.fixture_id}/commentary",
                params={"after_id": commentary[0]["id"]},
            )
        ).json()
        assert after == []


async def read_frames(
    lines: AsyncIterator[str], *, commentary_events: int, timeout: float = 3.0
) -> list[str]:
    """Read SSE frames until N commentary events have arrived."""
    frames: list[str] = []
    async with asyncio.timeout(timeout):
        async for line in lines:
            frames.append(line)
            if "data:" in line and sum(
                1 for f in frames if "data:" in f
            ) >= commentary_events:
                break
    return frames


async def test_sse_stream_catches_up_then_pushes_live() -> None:
    harness = await build_harness()
    first = await harness.commentary.insert(draft(harness.fixture_id, "catch-up line"))

    live_text = "live line"

    async def insert_soon() -> None:
        await asyncio.sleep(0.02)
        await harness.commentary.insert(draft(harness.fixture_id, live_text))

    inserter = asyncio.create_task(insert_soon())
    frames = await read_frames(
        commentary_sse(
            stream=StreamCommentary(commentary=harness.commentary, bus=harness.bus),
            fixture_id=harness.fixture_id,
            after_id=0,
            ping_seconds=0.05,
        ),
        commentary_events=2,
    )
    await inserter

    assert frames[0] == "retry: 3000\n\n"
    data_frames = [f for f in frames if "data:" in f]
    assert "catch-up line" in data_frames[0]
    assert live_text in data_frames[1]
    id_frames = [f.splitlines()[0] for f in frames if f.startswith("id:")]
    assert id_frames[0] == f"id: {first.id}"


async def test_sse_last_event_id_resumes() -> None:
    harness = await build_harness()
    first = await harness.commentary.insert(draft(harness.fixture_id, "old line"))
    await harness.commentary.insert(draft(harness.fixture_id, "new line"))

    frames = await read_frames(
        commentary_sse(
            stream=StreamCommentary(commentary=harness.commentary, bus=harness.bus),
            fixture_id=harness.fixture_id,
            after_id=first.id,
            ping_seconds=0.05,
        ),
        commentary_events=1,
    )

    data_frames = [f for f in frames if "data:" in f]
    assert len(data_frames) == 1
    assert "new line" in data_frames[0]
    assert "old line" not in data_frames[0]


async def test_sse_sends_keep_alive_when_idle() -> None:
    harness = await build_harness()
    frames: list[str] = []
    async with asyncio.timeout(3):
        async for line in commentary_sse(
            stream=StreamCommentary(commentary=harness.commentary, bus=harness.bus),
            fixture_id=harness.fixture_id,
            after_id=0,
            ping_seconds=0.05,
        ):
            frames.append(line)
            if any(f.startswith(": keep-alive") for f in frames):
                break
    assert any(f.startswith(": keep-alive") for f in frames)


async def test_sse_generator_propagates_pump_failure() -> None:
    class BrokenStream:
        async def run(
            self, *, fixture_id: int, after_id: int
        ) -> AsyncIterator[CommentaryMessage]:
            raise RuntimeError("stream blew up")
            yield  # pragma: no cover - marks this function as an async generator

    frames = commentary_sse(
        stream=BrokenStream(), fixture_id=1, after_id=0, ping_seconds=0.01
    )
    received = [await anext(frames)]  # retry frame
    with pytest.raises(RuntimeError, match="stream blew up"):
        async with asyncio.timeout(2):
            async for frame in frames:
                received.append(frame)
    assert received[0].startswith("retry:")


async def test_sse_generator_ends_when_stream_ends() -> None:
    message = CommentaryMessage(
        id=1,
        fixture_id=1,
        commentator_id=1,
        text="only line",
        triggering_event_id=None,
        in_reply_to=None,
        provider="fake",
        model="fake-1",
        usage=None,
        created_at=next_timestamp(),
    )

    class FiniteStream:
        async def run(
            self, *, fixture_id: int, after_id: int
        ) -> AsyncIterator[CommentaryMessage]:
            yield message

    frames = [
        frame
        async for frame in commentary_sse(
            stream=FiniteStream(), fixture_id=1, after_id=0, ping_seconds=0.01
        )
    ]
    assert frames[0].startswith("retry:")
    assert frames[1] == format_sse(message)
    assert len(frames) == 2


async def test_frontend_mount_serves_index() -> None:
    bus = InMemoryBus()
    commentary = InMemoryCommentaryRepository(bus=bus)
    deps = WebDeps(
        fixtures=InMemoryFixtureRepository(),
        events=InMemoryEventRepository(bus=bus),
        commentary=commentary,
        commentators=InMemoryCommentatorRepository(),
        stream=StreamCommentary(commentary=commentary, bus=bus),
        sse_ping_seconds=0.05,
    )
    app = create_app(deps=deps, frontend_dir=FRONTEND_DIR)
    assert any(getattr(route, "name", None) == "frontend" for route in app.routes)
    assert "Live AI Football Commentary" in (FRONTEND_DIR / "index.html").read_text(
        encoding="utf-8"
    )

    with pytest.raises(FileNotFoundError, match="frontend"):
        create_app(deps=deps, frontend_dir=FRONTEND_DIR / "missing")
