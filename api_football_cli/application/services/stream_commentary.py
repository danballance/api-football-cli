"""StreamCommentary: catch-up replay then live tail (architecture §9).

Subscribes before selecting so nothing can slip between the catch-up query
and the live notifications; every wake-up re-selects ``id > last`` which makes
the stream gap-proof and duplicate-free.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from api_football_cli.application.ports.event_bus import COMMENTARY_CHANNEL, EventBus
from api_football_cli.application.ports.repositories import CommentaryRepository
from api_football_cli.domain.entities import CommentaryMessage


class StreamCommentary:
    def __init__(self, *, commentary: CommentaryRepository, bus: EventBus) -> None:
        self._commentary = commentary
        self._bus = bus

    async def run(
        self, *, fixture_id: int, after_id: int
    ) -> AsyncIterator[CommentaryMessage]:
        async with self._bus.subscribe(COMMENTARY_CHANNEL) as notifications:
            last_seen = after_id
            for message in await self._commentary.list_after(
                fixture_id=fixture_id, after_message_id=last_seen
            ):
                last_seen = message.id
                yield message
            async for notification in notifications:
                if notification.fixture_id != fixture_id:
                    continue
                for message in await self._commentary.list_after(
                    fixture_id=fixture_id, after_message_id=last_seen
                ):
                    last_seen = message.id
                    yield message
