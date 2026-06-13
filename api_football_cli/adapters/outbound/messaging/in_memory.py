"""InMemoryBus: process-local EventBus for tests and offline pipelines."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from api_football_cli.application.ports.event_bus import Notification


class InMemoryBus:
    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue[Notification]]] = {}

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[AsyncIterator[Notification]]:
        queue: asyncio.Queue[Notification] = asyncio.Queue()
        self._queues.setdefault(channel, set()).add(queue)
        try:
            yield _drain(queue)
        finally:
            self._queues[channel].discard(queue)

    async def publish(self, channel: str, notification: Notification) -> None:
        for queue in self._queues.get(channel, set()):
            queue.put_nowait(notification)


async def _drain(queue: asyncio.Queue[Notification]) -> AsyncIterator[Notification]:
    while True:
        yield await queue.get()
