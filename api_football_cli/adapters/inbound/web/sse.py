"""Hand-rolled Server-Sent Events for whole-message commentary delivery.

The wire format is the standard EventSource contract (id / event / data plus
comment heartbeats), so browsers get auto-reconnect with Last-Event-ID replay
for free. Doing it by hand keeps the delivery path explicit and deterministic
under test — no framework state, no magic.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

from api_football_cli.adapters.inbound.web.schemas import CommentaryDTO
from api_football_cli.domain.entities import CommentaryMessage

SSE_RETRY_MILLIS = 3000
KEEP_ALIVE_COMMENT = ": keep-alive\n\n"


class CommentaryStream(Protocol):
    """What the SSE generator needs from StreamCommentary."""

    def run(self, *, fixture_id: int, after_id: int) -> AsyncIterator[CommentaryMessage]: ...


def format_sse(message: CommentaryMessage) -> str:
    dto = CommentaryDTO.from_domain(message)
    return f"id: {message.id}\nevent: commentary\ndata: {dto.model_dump_json()}\n\n"


async def commentary_sse(
    *,
    stream: CommentaryStream,
    fixture_id: int,
    after_id: int,
    ping_seconds: float,
) -> AsyncIterator[str]:
    """Yield SSE frames: catch-up replay, then live messages, with heartbeats."""
    yield f"retry: {SSE_RETRY_MILLIS}\n\n"

    queue: asyncio.Queue[CommentaryMessage] = asyncio.Queue()

    async def pump() -> None:
        async for message in stream.run(fixture_id=fixture_id, after_id=after_id):
            await queue.put(message)

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            try:
                async with asyncio.timeout(ping_seconds):
                    message = await queue.get()
            except TimeoutError:
                if pump_task.done():
                    error = pump_task.exception()
                    if error is not None:
                        raise error from None
                    return
                yield KEEP_ALIVE_COMMENT
                continue
            yield format_sse(message)
    finally:
        pump_task.cancel()
