"""IngestFixtureEvents: the polling loop (architecture §6).

    Ingestion knows nothing about commentary or the browser; it keeps the DB
    current and reactivity flows from insert → NOTIFY (Postgres trigger).
"""

from __future__ import annotations

import asyncio

from api_football_cli.application.ports.football_api import FootballApi
from api_football_cli.application.ports.repositories import (
    ApiRequestLogRepository,
    EventRepository,
    FixtureRepository,
)
from api_football_cli.domain.entities import TERMINAL_STATUSES, Fixture

POLL_ENDPOINT_LABEL = "fixtures+fixtures/events"


class QuotaExhaustedError(RuntimeError):
    """Raised (fail fast) when the daily quota drops to the configured floor."""


class IngestFixtureEvents:
    def __init__(
        self,
        *,
        api: FootballApi,
        fixtures: FixtureRepository,
        events: EventRepository,
        request_log: ApiRequestLogRepository,
        interval_seconds: float,
        quota_floor: int,
    ) -> None:
        if interval_seconds < 0:
            raise ValueError(f"interval_seconds must be >= 0, got {interval_seconds}")
        if quota_floor < 0:
            raise ValueError(f"quota_floor must be >= 0, got {quota_floor}")
        self._api = api
        self._fixtures = fixtures
        self._events = events
        self._request_log = request_log
        self._interval_seconds = interval_seconds
        self._quota_floor = quota_floor

    async def poll_once(self, api_fixture_id: int) -> Fixture:
        """One poll cycle: refresh the fixture row and append any new events."""
        snapshot = await self._api.fixture(api_fixture_id)
        fixture = await self._fixtures.upsert_snapshot(snapshot)
        observed = await self._api.fixtures_events(api_fixture_id)
        for event in observed:
            await self._events.insert_if_new(fixture_id=fixture.id, event=event)

        remaining = self._api.requests_remaining()
        await self._request_log.record(
            endpoint=POLL_ENDPOINT_LABEL, requests_remaining=remaining
        )
        if remaining <= self._quota_floor:
            raise QuotaExhaustedError(
                f"api-football daily quota at {remaining} requests "
                f"(floor {self._quota_floor}); stopping before it runs out"
            )
        return fixture

    async def run(self, api_fixture_id: int) -> Fixture:
        """Poll until the fixture reaches a terminal status; returns its final state."""
        while True:
            fixture = await self.poll_once(api_fixture_id)
            if fixture.status in TERMINAL_STATUSES:
                return fixture
            await asyncio.sleep(self._interval_seconds)
