"""IngestFixtureEvents: polling, dedup, quota fail-fast, terminal stop."""

from __future__ import annotations

import pytest

from api_football_cli.application.services.ingest_events import (
    IngestFixtureEvents,
    QuotaExhaustedError,
)
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
    *,
    quota_floor: int | None,
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
        quota_floor=quota_floor,
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
    service, fixtures, events, log = build_service(api, quota_floor=5)

    final = await service.run(1001)

    assert final.status is FixtureStatus.FULL_TIME
    assert [stored.event for stored in events.stored] == [goal, second_goal]
    assert len(log.records) == 3
    assert log.records[0] == ("fixtures+fixtures/events", 100)
    assert len(await fixtures.list_all()) == 1


async def test_quota_floor_fails_fast() -> None:
    api = StubFootballApi(
        snapshots=[make_snapshot(status=FixtureStatus.FIRST_HALF, elapsed=10)],
        event_batches=[[]],
        remaining=3,
    )
    service, _, _, log = build_service(api, quota_floor=5)
    with pytest.raises(QuotaExhaustedError, match="floor 5"):
        await service.poll_once(1001)
    assert log.records == [("fixtures+fixtures/events", 3)]


async def test_unknown_quota_skips_the_floor_check() -> None:
    api = StubFootballApi(
        snapshots=[make_snapshot(status=FixtureStatus.FULL_TIME, elapsed=90)],
        event_batches=[[]],
        remaining=None,
    )
    service, _, _, log = build_service(api, quota_floor=5)
    fixture = await service.run(1001)
    assert fixture.status is FixtureStatus.FULL_TIME
    assert log.records == [("fixtures+fixtures/events", None)]


def test_negative_interval_rejected() -> None:
    api = StubFootballApi(snapshots=[], event_batches=[], remaining=None)
    with pytest.raises(ValueError, match="interval_seconds"):
        IngestFixtureEvents(
            api=api,
            fixtures=InMemoryFixtureRepository(),
            events=InMemoryEventRepository(bus=None),
            request_log=InMemoryRequestLog(),
            interval_seconds=-1,
            quota_floor=None,
        )
