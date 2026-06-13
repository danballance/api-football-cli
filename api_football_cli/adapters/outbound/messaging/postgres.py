"""PostgresListenNotifyBus: LISTEN/NOTIFY over one dedicated connection.

The adapter holds a single long-lived raw asyncpg connection (a LISTEN does
not belong in an ORM session/pool — architecture §5) and fans incoming
payloads out to local subscriber queues. Publishing uses pg_notify, although
in production the AFTER INSERT triggers are the usual notification source.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Protocol

from api_football_cli.application.ports.event_bus import BusError, EventBus, Notification

ListenCallback = Callable[[object, object, object, object], None]


class NotifyConnection(Protocol):
    """The slice of an asyncpg connection this adapter needs."""

    async def add_listener(self, channel: str, callback: ListenCallback) -> None: ...

    async def execute(self, query: str, *args: object) -> str: ...

    async def close(self) -> None: ...


Connector = Callable[[], Coroutine[None, None, NotifyConnection]]


class PostgresListenNotifyBus(EventBus):
    def __init__(self, connect: Connector) -> None:
        self._connect = connect
        self._connection: NotifyConnection | None = None
        self._queues: dict[str, set[asyncio.Queue[str]]] = {}
        self._listened: set[str] = set()

    async def start(self) -> None:
        self._connection = await self._connect()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    def _require_connection(self) -> NotifyConnection:
        if self._connection is None:
            raise BusError("PostgresListenNotifyBus used before start()")
        return self._connection

    def _on_notify(self, _conn: object, _pid: object, channel: object, payload: object) -> None:
        for queue in self._queues.get(str(channel), set()):
            queue.put_nowait(str(payload))

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[AsyncIterator[Notification]]:
        connection = self._require_connection()
        if channel not in self._listened:
            await connection.add_listener(channel, self._on_notify)
            self._listened.add(channel)
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._queues.setdefault(channel, set()).add(queue)
        try:
            yield _parse(queue)
        finally:
            self._queues[channel].discard(queue)

    async def publish(self, channel: str, notification: Notification) -> None:
        connection = self._require_connection()
        await connection.execute(
            "SELECT pg_notify($1, $2)", channel, notification.model_dump_json()
        )


async def _parse(queue: asyncio.Queue[str]) -> AsyncIterator[Notification]:
    while True:
        payload = await queue.get()
        # Payloads come from our own triggers; anything else must blow up loudly.
        yield Notification.model_validate_json(payload)
