"""End-to-end replay pipeline: ingest -> bus -> rounds -> stream.

Uses the shipped demo replay, in-memory ports (the event/commentary repos
publish on insert exactly like the Postgres triggers), and the FakeModel —
the full event-driven path with zero network, zero model spend.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from api_football_cli.adapters.outbound.apifootball.fake import FakeFootballApi, ReplayFile
from api_football_cli.adapters.outbound.messaging.in_memory import InMemoryBus
from api_football_cli.adapters.outbound.model.fake import DEFAULT_FAKE_LINES, FakeModel
from api_football_cli.application.services.generate_commentary import (
    CommentaryWorker,
    GenerateCommentaryRound,
)
from api_football_cli.application.services.ingest_events import IngestFixtureEvents
from api_football_cli.application.services.stream_commentary import StreamCommentary
from api_football_cli.domain.entities import CommentaryMessage, FixtureStatus
from api_football_cli.domain.personas import PERSONAS

from tests.fakes import (
    InMemoryCommentaryRepository,
    InMemoryCommentatorRepository,
    InMemoryEventRepository,
    InMemoryFixtureRepository,
    InMemoryRequestLog,
)

DEMO_REPLAY = Path(__file__).resolve().parents[1] / "examples" / "replay-demo.json"


async def test_full_replay_pipeline() -> None:
    bus = InMemoryBus()
    fixtures = InMemoryFixtureRepository()
    events = InMemoryEventRepository(bus=bus)
    commentary = InMemoryCommentaryRepository(bus=bus)
    commentator_repo = InMemoryCommentatorRepository()
    booth = [await commentator_repo.upsert(seed) for seed in PERSONAS]

    api = FakeFootballApi(replay=ReplayFile.load(DEMO_REPLAY), minutes_per_poll=30)
    fixture = await fixtures.upsert_snapshot(await api.fixture(999001))

    ingest = IngestFixtureEvents(
        api=api,
        fixtures=fixtures,
        events=events,
        request_log=InMemoryRequestLog(),
        interval_seconds=0,
        quota_floor=None,
    )
    rounds = GenerateCommentaryRound(
        fixtures=fixtures,
        events=events,
        commentary=commentary,
        model=FakeModel(lines=DEFAULT_FAKE_LINES),
        commentators=booth,
        max_messages_per_round=2,
    )
    worker = CommentaryWorker(bus=bus, rounds=rounds, fixture_id=fixture.id)
    stream = StreamCommentary(commentary=commentary, bus=bus)

    streamed: list[CommentaryMessage] = []

    async def consume() -> None:
        async for message in stream.run(fixture_id=fixture.id, after_id=0):
            streamed.append(message)

    worker_task = asyncio.create_task(worker.run())
    consumer_task = asyncio.create_task(consume())
    try:
        await asyncio.sleep(0.05)  # let subscriptions register

        final = await ingest.run(999001)
        assert final.status is FixtureStatus.FULL_TIME

        # All 10 recorded events are appended exactly once.
        assert len(events.stored) == 10
        last_event_id = events.stored[-1].id

        # Wait until the worker has commented through the final event.
        async with asyncio.timeout(5):
            while await commentary.last_triggering_event_id(fixture.id) < last_event_id:
                await asyncio.sleep(0.01)

        messages = await commentary.list_for_fixture(fixture.id)
        assert messages, "expected commentary to be generated"
        assert len(messages) % 2 == 0  # whole rounds only
        # Every round: play-by-play first, colour reply linked to it.
        for pbp, color in zip(messages[::2], messages[1::2], strict=True):
            assert pbp.commentator_id == booth[0].id
            assert color.commentator_id == booth[1].id
            assert color.in_reply_to == pbp.id

        # The SSE-facing stream delivered every message, in order.
        async with asyncio.timeout(5):
            while len(streamed) < len(messages):
                await asyncio.sleep(0.01)
        assert [m.id for m in streamed] == [m.id for m in messages]
    finally:
        worker_task.cancel()
        consumer_task.cancel()
