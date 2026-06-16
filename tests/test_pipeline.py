"""End-to-end pipeline: ingest -> rounds -> stream."""

from __future__ import annotations

import asyncio

from api_football_cli.adapters.outbound.messaging.in_memory import InMemoryBus
from api_football_cli.adapters.outbound.model.fake import DEFAULT_FAKE_LINES, FakeModel
from api_football_cli.application.services.generate_commentary import GenerateCommentaryRound
from api_football_cli.application.services.ingest_events import IngestFixtureEvents
from api_football_cli.application.services.stream_commentary import StreamCommentary
from api_football_cli.domain.entities import CommentaryMessage, FixtureStatus
from api_football_cli.domain.personas import PERSONAS
from tests.factories import AWAY, make_event, make_snapshot
from tests.fakes import (
    InMemoryCommentaryRepository,
    InMemoryCommentatorRepository,
    InMemoryEventRepository,
    InMemoryFixtureRepository,
    InMemoryRequestLog,
    StubFootballApi,
)


async def test_full_pipeline() -> None:
    bus = InMemoryBus()
    fixtures = InMemoryFixtureRepository()
    events = InMemoryEventRepository(bus=bus)
    commentary = InMemoryCommentaryRepository(bus=bus)
    commentator_repo = InMemoryCommentatorRepository()
    booth = [await commentator_repo.upsert(seed) for seed in PERSONAS]

    api = StubFootballApi(
        snapshots=[
            make_snapshot(api_fixture_id=999001, status=FixtureStatus.FIRST_HALF, elapsed=12),
            make_snapshot(
                api_fixture_id=999001,
                status=FixtureStatus.SECOND_HALF,
                elapsed=61,
                home_goals=1,
                away_goals=1,
            ),
            make_snapshot(
                api_fixture_id=999001,
                status=FixtureStatus.FULL_TIME,
                elapsed=90,
                home_goals=2,
                away_goals=1,
            ),
        ],
        event_batches=[
            [make_event(elapsed=9)],
            [make_event(elapsed=9), make_event(elapsed=33, team=AWAY)],
            [
                make_event(elapsed=9),
                make_event(elapsed=33, team=AWAY),
                make_event(elapsed=67, detail="Penalty"),
            ],
        ],
        remaining=100,
    )

    fixture = await fixtures.upsert_snapshot(make_snapshot(api_fixture_id=999001))

    ingest = IngestFixtureEvents(
        api=api,
        fixtures=fixtures,
        events=events,
        request_log=InMemoryRequestLog(),
        interval_seconds=0,
    )
    rounds = GenerateCommentaryRound(
        fixtures=fixtures,
        events=events,
        commentary=commentary,
        model=FakeModel(lines=DEFAULT_FAKE_LINES),
        commentators=booth,
        max_messages_per_round=2,
    )
    stream = StreamCommentary(commentary=commentary, bus=bus)

    streamed: list[CommentaryMessage] = []

    async def consume() -> None:
        async for message in stream.run(fixture_id=fixture.id, after_id=0):
            streamed.append(message)

    consumer_task = asyncio.create_task(consume())
    try:
        await asyncio.sleep(0.05)

        first = await ingest.poll_once(999001)
        assert first.status is FixtureStatus.FIRST_HALF
        produced = await rounds.run(fixture.id)
        assert produced

        second = await ingest.poll_once(999001)
        assert second.status is FixtureStatus.SECOND_HALF
        produced = await rounds.run(fixture.id)
        assert produced

        final = await ingest.poll_once(999001)
        assert final.status is FixtureStatus.FULL_TIME
        produced = await rounds.run(fixture.id)
        assert produced

        assert len(events.stored) == 3

        messages = await commentary.list_for_fixture(fixture.id)
        assert messages
        assert len(messages) % 2 == 0
        for pbp, color in zip(messages[::2], messages[1::2], strict=True):
            assert pbp.commentator_id == booth[0].id
            assert color.commentator_id == booth[1].id
            assert color.in_reply_to == pbp.id

        async with asyncio.timeout(5):
            while len(streamed) < len(messages):
                await asyncio.sleep(0.01)
        assert [m.id for m in streamed] == [m.id for m in messages]
    finally:
        consumer_task.cancel()
