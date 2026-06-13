"""Commentary rounds: coalescing, ordering, transcripts, the worker loop."""

from __future__ import annotations

import asyncio

from api_football_cli.adapters.outbound.messaging.in_memory import InMemoryBus
from api_football_cli.application.ports.event_bus import (
    FIXTURE_EVENTS_CHANNEL,
    Notification,
)
from api_football_cli.application.services.generate_commentary import (
    CommentaryWorker,
    GenerateCommentaryRound,
)
from api_football_cli.domain.entities import SpeakerRole
from tests.factories import AWAY, BOOTH, make_event, make_snapshot
from tests.fakes import (
    InMemoryCommentaryRepository,
    InMemoryEventRepository,
    InMemoryFixtureRepository,
    RecordingModel,
)


async def build_round(
    *, bus: InMemoryBus | None
) -> tuple[
    GenerateCommentaryRound,
    InMemoryFixtureRepository,
    InMemoryEventRepository,
    InMemoryCommentaryRepository,
    RecordingModel,
    int,
]:
    fixtures = InMemoryFixtureRepository()
    events = InMemoryEventRepository(bus=bus)
    commentary = InMemoryCommentaryRepository(bus=bus)
    model = RecordingModel()
    fixture = await fixtures.upsert_snapshot(make_snapshot())
    rounds = GenerateCommentaryRound(
        fixtures=fixtures,
        events=events,
        commentary=commentary,
        model=model,
        commentators=BOOTH,
        max_messages_per_round=2,
    )
    return rounds, fixtures, events, commentary, model, fixture.id


async def test_round_produces_pbp_then_color_and_links_reply() -> None:
    rounds, _, events, commentary, model, fixture_id = await build_round(bus=None)
    stored = await events.insert_if_new(fixture_id=fixture_id, event=make_event())
    assert stored is not None

    produced = await rounds.run(fixture_id)

    assert [m.commentator_id for m in produced] == [1, 2]
    assert produced[0].triggering_event_id == stored.id
    assert produced[0].in_reply_to is None
    assert produced[1].in_reply_to == produced[0].id
    assert produced[0].provider == "recording"
    assert produced[0].model == "recorder-1"

    # The colour commentator's transcript contains the feed line plus the
    # play-by-play line tagged as the colleague's.
    color_system, color_transcript = model.calls[1]
    assert "Robbie Banks" in color_system
    assert "Riverton Albion vs Kingsport Wanderers" in color_system
    speakers = [t.speaker for t in color_transcript]
    assert speakers == [SpeakerRole.MATCH_FEED, SpeakerRole.CO_COMMENTATOR]


async def test_round_with_no_new_events_is_a_noop() -> None:
    rounds, _, _, commentary, model, fixture_id = await build_round(bus=None)
    assert await rounds.run(fixture_id) == []
    assert model.calls == []
    assert commentary.messages == []


async def test_rounds_coalesce_multiple_events() -> None:
    rounds, _, events, commentary, _, fixture_id = await build_round(bus=None)
    await events.insert_if_new(fixture_id=fixture_id, event=make_event())
    second = await events.insert_if_new(
        fixture_id=fixture_id, event=make_event(elapsed=11, team=AWAY)
    )
    assert second is not None

    produced = await rounds.run(fixture_id)
    assert len(produced) == 2
    assert produced[0].triggering_event_id == second.id

    # The next round sees nothing new.
    assert await rounds.run(fixture_id) == []
    assert await commentary.last_triggering_event_id(fixture_id) == second.id


async def test_second_round_sees_own_lines_in_perspective() -> None:
    rounds, _, events, _, model, fixture_id = await build_round(bus=None)
    await events.insert_if_new(fixture_id=fixture_id, event=make_event())
    await rounds.run(fixture_id)
    await events.insert_if_new(
        fixture_id=fixture_id, event=make_event(elapsed=44, type_="Card", detail="Yellow Card")
    )
    await rounds.run(fixture_id)

    pbp_system, pbp_transcript = model.calls[2]
    assert "Marty Vox" in pbp_system
    assert [t.speaker for t in pbp_transcript] == [
        SpeakerRole.MATCH_FEED,
        SpeakerRole.THIS_COMMENTATOR,
        SpeakerRole.CO_COMMENTATOR,
        SpeakerRole.MATCH_FEED,
    ]


async def test_worker_runs_rounds_for_its_fixture_only() -> None:
    bus = InMemoryBus()
    rounds, _, events, commentary, _, fixture_id = await build_round(bus=bus)
    worker = CommentaryWorker(bus=bus, rounds=rounds, fixture_id=fixture_id)

    worker_task = asyncio.create_task(worker.run())
    try:
        await asyncio.sleep(0.05)  # let the worker subscribe

        # Insert publishes a notification (mirroring the DB trigger).
        await events.insert_if_new(fixture_id=fixture_id, event=make_event())
        async with asyncio.timeout(2):
            while len(commentary.messages) < 2:
                await asyncio.sleep(0.01)

        # A notification for another fixture is ignored.
        await bus.publish(FIXTURE_EVENTS_CHANNEL, Notification(fixture_id=999, id=1))
        await asyncio.sleep(0.05)
        assert len(commentary.messages) == 2
    finally:
        worker_task.cancel()


async def test_worker_catches_up_on_start() -> None:
    bus = InMemoryBus()
    rounds, _, events, commentary, _, fixture_id = await build_round(bus=bus)
    # Event inserted BEFORE the worker subscribes: no notification will come.
    await events.insert_if_new(fixture_id=fixture_id, event=make_event())

    worker = CommentaryWorker(bus=bus, rounds=rounds, fixture_id=fixture_id)
    worker_task = asyncio.create_task(worker.run())
    try:
        async with asyncio.timeout(2):
            while len(commentary.messages) < 2:
                await asyncio.sleep(0.01)
    finally:
        worker_task.cancel()
