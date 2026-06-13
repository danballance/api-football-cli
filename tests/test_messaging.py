"""EventBus adapters: in-memory and Postgres LISTEN/NOTIFY (faked connection)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from api_football_cli.adapters.outbound.messaging.in_memory import InMemoryBus
from api_football_cli.adapters.outbound.messaging.postgres import (
    PostgresListenNotifyBus,
)
from api_football_cli.application.ports.event_bus import (
    COMMENTARY_CHANNEL,
    FIXTURE_EVENTS_CHANNEL,
    BusError,
    Notification,
)

NOTE = Notification(fixture_id=1, id=10)


async def test_in_memory_bus_fans_out_to_all_subscribers() -> None:
    bus = InMemoryBus()
    async with bus.subscribe(FIXTURE_EVENTS_CHANNEL) as first:
        async with bus.subscribe(FIXTURE_EVENTS_CHANNEL) as second:
            await bus.publish(FIXTURE_EVENTS_CHANNEL, NOTE)
            assert await asyncio.wait_for(anext(first), 1) == NOTE
            assert await asyncio.wait_for(anext(second), 1) == NOTE


async def test_in_memory_bus_isolates_channels_and_unsubscribes() -> None:
    bus = InMemoryBus()
    async with bus.subscribe(FIXTURE_EVENTS_CHANNEL) as events:
        await bus.publish(COMMENTARY_CHANNEL, NOTE)  # nobody listening: dropped
        await bus.publish(FIXTURE_EVENTS_CHANNEL, NOTE)
        assert await asyncio.wait_for(anext(events), 1) == NOTE
    # After the context exits the queue is detached; publishing must not error.
    await bus.publish(FIXTURE_EVENTS_CHANNEL, NOTE)


class FakeAsyncpgConnection:
    """Mimics the slice of asyncpg.Connection the bus uses."""

    def __init__(self) -> None:
        self.listeners: dict[str, Callable[[object, object, object, object], None]] = {}
        self.executed: list[tuple[str, str, str]] = []
        self.closed = False

    async def add_listener(
        self, channel: str, callback: Callable[[object, object, object, object], None]
    ) -> None:
        self.listeners[channel] = callback

    async def execute(self, query: str, *args: object) -> str:
        channel, payload = str(args[0]), str(args[1])
        self.executed.append((query, channel, payload))
        if channel in self.listeners:
            self.listeners[channel](self, 1234, channel, payload)
        return "SELECT 1"

    async def close(self) -> None:
        self.closed = True


async def _never_connect() -> FakeAsyncpgConnection:
    raise AssertionError("connector must not be called")


async def test_postgres_bus_requires_start() -> None:
    bus = PostgresListenNotifyBus(_never_connect)
    with pytest.raises(BusError, match="start"):
        await bus.publish(FIXTURE_EVENTS_CHANNEL, NOTE)


async def test_postgres_bus_listen_publish_roundtrip() -> None:
    connection = FakeAsyncpgConnection()

    async def connect() -> FakeAsyncpgConnection:
        return connection

    bus = PostgresListenNotifyBus(connect)
    await bus.start()

    async with bus.subscribe(FIXTURE_EVENTS_CHANNEL) as notifications:
        assert FIXTURE_EVENTS_CHANNEL in connection.listeners
        await bus.publish(FIXTURE_EVENTS_CHANNEL, NOTE)
        received = await asyncio.wait_for(anext(notifications), 1)
        assert received == NOTE

    query, channel, payload = connection.executed[0]
    assert "pg_notify" in query
    assert channel == FIXTURE_EVENTS_CHANNEL
    assert '"fixture_id":1' in payload

    await bus.close()
    assert connection.closed


async def test_postgres_bus_trigger_payloads_reach_all_queues() -> None:
    connection = FakeAsyncpgConnection()

    async def connect() -> FakeAsyncpgConnection:
        return connection

    bus = PostgresListenNotifyBus(connect)
    await bus.start()

    async with bus.subscribe(COMMENTARY_CHANNEL) as first:
        async with bus.subscribe(COMMENTARY_CHANNEL) as second:
            # Simulate a payload arriving from a database trigger.
            connection.listeners[COMMENTARY_CHANNEL](
                connection, 99, COMMENTARY_CHANNEL, NOTE.model_dump_json()
            )
            assert await asyncio.wait_for(anext(first), 1) == NOTE
            assert await asyncio.wait_for(anext(second), 1) == NOTE
