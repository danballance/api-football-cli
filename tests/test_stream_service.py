"""StreamCommentary: catch-up replay then live tail, gap-proof."""

from __future__ import annotations

import asyncio

from api_football_cli.adapters.outbound.messaging.in_memory import InMemoryBus
from api_football_cli.application.services.stream_commentary import StreamCommentary
from api_football_cli.domain.entities import CommentaryDraft
from tests.fakes import InMemoryCommentaryRepository


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


async def test_stream_replays_then_tails_live() -> None:
    bus = InMemoryBus()
    commentary = InMemoryCommentaryRepository(bus=bus)
    await commentary.insert(draft(1, "before-1"))
    await commentary.insert(draft(1, "before-2"))
    await commentary.insert(draft(2, "other fixture"))

    stream = StreamCommentary(commentary=commentary, bus=bus)
    received: list[str] = []

    async def consume() -> None:
        async for message in stream.run(fixture_id=1, after_id=0):
            received.append(message.text)
            if len(received) == 4:
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # catch-up done, subscription live
    assert received == ["before-1", "before-2"]

    await commentary.insert(draft(2, "still other fixture"))
    await commentary.insert(draft(1, "live-1"))
    await commentary.insert(draft(1, "live-2"))

    await asyncio.wait_for(consumer, 2)
    assert received == ["before-1", "before-2", "live-1", "live-2"]


async def test_stream_resumes_after_id() -> None:
    bus = InMemoryBus()
    commentary = InMemoryCommentaryRepository(bus=bus)
    first = await commentary.insert(draft(1, "first"))
    await commentary.insert(draft(1, "second"))

    stream = StreamCommentary(commentary=commentary, bus=bus)

    async def take_one() -> str:
        async for message in stream.run(fixture_id=1, after_id=first.id):
            return message.text
        raise AssertionError("stream ended unexpectedly")

    assert await asyncio.wait_for(take_one(), 2) == "second"
