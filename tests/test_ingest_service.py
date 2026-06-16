"""IngestFixtureEvents: polling, dedup, request logging, terminal stop."""

from __future__ import annotations

import pytest

from api_football_cli.application.services.ingest_events import IngestFixtureEvents
from api_football_cli.domain.entities import FixtureStatus
from tests.factories import AWAY, make_event, make_snapshot
from tests.fakes import (
    InMemoryEventRepository,
    InMemoryFixtureRepository,
    InMemoryRequestLog,
    StubFootballApi,
)


def build_service(
    api: StubFootballApi,
) -> tuple[
    IngestFixtureEvents, InMemoryFixtureRepository, InMemoryEventRepository, InMemoryRequestLog
]:
    fixtures = InMemoryFixtureRepository()
    events = InMemoryEventRepository(bus=None)
    log = InMemoryRequestLog()
    service = IngestFixtureEvents(
        api=api,
        fixtures=fixtures,
        events=events,
        request_log=log,
        interval_seconds=0,
    )
    return service, fixtures, events, log


async def test_run_polls_until_terminal_and_appends_once() -> None:
    goal = make_event()
    second_goal = make_event(elapsed=33, team=AWAY)
    api = StubFootballApi(
        snapshots=[
            make_snapshot(status=FixtureStatus.FIRST_HALF, elapsed=10),
            make_snapshot(status=FixtureStatus.SECOND_HALF, elapsed=60),
            make_snapshot(status=FixtureStatus.FULL_TIME, elapsed=90),
        ],
        event_batches=[[goal], [goal, second_goal], [goal, second_goal]],
        remaining=100,
    )
    service, fixtures, events, log = build_service(api)

    final = await service.run(1001)

    assert final.status is FixtureStatus.FULL_TIME
    assert [stored.event for stored in events.stored] == [goal, second_goal]
    assert len(log.records) == 3
    assert log.records[0] == ("fixtures+fixtures/events", 100)
    assert len(await fixtures.list_all()) == 1


async def test_poll_once_logs_remaining_quota() -> None:
    api = StubFootballApi(
        snapshots=[make_snapshot(status=FixtureStatus.FIRST_HALF, elapsed=10)],
        event_batches=[[]],
        remaining=3,
    )
    service, _, _, log = build_service(api)

    await service.poll_once(1001)

    assert log.records == [("fixtures+fixtures/events", 3)]


def test_negative_interval_rejected() -> None:
    api = StubFootballApi(snapshots=[], event_batches=[], remaining=None)
    with pytest.raises(ValueError, match="interval_seconds"):
        IngestFixtureEvents(
            api=api,
            fixtures=InMemoryFixtureRepository(),
            events=InMemoryEventRepository(bus=None),
            request_log=InMemoryRequestLog(),
            interval_seconds=-1,
        )
