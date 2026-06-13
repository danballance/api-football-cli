"""Booth transcript construction and perspective tagging."""

from __future__ import annotations

from api_football_cli.domain.entities import (
    CommentaryMessage,
    SpeakerRole,
    StoredFixtureEvent,
)
from api_football_cli.domain.transcript import build_transcript

from tests.factories import make_event
from tests.fakes import next_timestamp


def _stored(event_id: int) -> StoredFixtureEvent:
    return StoredFixtureEvent(
        id=event_id,
        fixture_id=1,
        event_hash=f"hash-{event_id}",
        event=make_event(elapsed=event_id),
        created_at=next_timestamp(),
    )


def _message(message_id: int, commentator_id: int, text: str) -> CommentaryMessage:
    return CommentaryMessage(
        id=message_id,
        fixture_id=1,
        commentator_id=commentator_id,
        text=text,
        triggering_event_id=None,
        in_reply_to=None,
        provider="fake",
        model="fake-1",
        usage=None,
        created_at=next_timestamp(),
    )


def test_transcript_interleaves_in_creation_order_and_tags_perspective() -> None:
    event_one = _stored(1)
    message_one = _message(1, commentator_id=1, text="What a goal!")
    message_two = _message(2, commentator_id=2, text="Shocking defending.")
    event_two = _stored(2)

    transcript = build_transcript(
        events=[event_one, event_two],
        messages=[message_one, message_two],
        perspective_commentator_id=1,
    )

    assert [t.speaker for t in transcript] == [
        SpeakerRole.MATCH_FEED,
        SpeakerRole.THIS_COMMENTATOR,
        SpeakerRole.CO_COMMENTATOR,
        SpeakerRole.MATCH_FEED,
    ]
    assert transcript[1].text == "What a goal!"


def test_transcript_swaps_tags_for_other_perspective() -> None:
    message = _message(3, commentator_id=1, text="My line")
    transcript = build_transcript(
        events=[], messages=[message], perspective_commentator_id=2
    )
    assert len(transcript) == 1
    assert transcript[0].speaker is SpeakerRole.CO_COMMENTATOR


def test_events_sort_before_messages_at_equal_timestamps() -> None:
    shared_time = next_timestamp()
    event = StoredFixtureEvent(
        id=9,
        fixture_id=1,
        event_hash="h",
        event=make_event(),
        created_at=shared_time,
    )
    message = CommentaryMessage(
        id=9,
        fixture_id=1,
        commentator_id=1,
        text="tied",
        triggering_event_id=None,
        in_reply_to=None,
        provider="fake",
        model="fake-1",
        usage=None,
        created_at=shared_time,
    )
    transcript = build_transcript(
        events=[event], messages=[message], perspective_commentator_id=1
    )
    assert transcript[0].speaker is SpeakerRole.MATCH_FEED
    assert transcript[1].speaker is SpeakerRole.THIS_COMMENTATOR
