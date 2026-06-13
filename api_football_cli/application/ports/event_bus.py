"""The EventBus port: the reactive spine (architecture §5).

In production notifications originate from Postgres AFTER INSERT triggers and
are delivered over LISTEN/NOTIFY; payloads are tiny (our row ids only).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from api_football_cli.domain.entities import FrozenModel

FIXTURE_EVENTS_CHANNEL = "fixture_event_inserted"
COMMENTARY_CHANNEL = "commentary_inserted"


class BusError(RuntimeError):
    """Raised when the bus is used in an invalid state."""


class Notification(FrozenModel):
    """What a NOTIFY payload carries: the fixture and the new row id."""

    fixture_id: int
    id: int


class EventBus(Protocol):
    def subscribe(
        self, channel: str
    ) -> AbstractAsyncContextManager[AsyncIterator[Notification]]:
        """Subscribe to a channel; yields notifications until the context exits."""
        ...

    async def publish(self, channel: str, notification: Notification) -> None: ...
