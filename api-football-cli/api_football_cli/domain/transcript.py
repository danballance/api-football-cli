"""Build the booth transcript for one commentator's perspective.

Both commentators see the same interleaved history of match-feed lines and
commentary; the only difference per perspective is the tagging of "my line"
vs "my colleague's line" (architecture §8).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from api_football_cli.domain.entities import (
    CommentaryMessage,
    SpeakerRole,
    StoredFixtureEvent,
    Turn,
)
from api_football_cli.domain.events import render_event

# Events sort before commentary at equal timestamps: a round's lines always
# follow the feed lines that triggered them.
_KIND_EVENT = 0
_KIND_MESSAGE = 1


def build_transcript(
    *,
    events: Sequence[StoredFixtureEvent],
    messages: Sequence[CommentaryMessage],
    perspective_commentator_id: int,
) -> list[Turn]:
    items: list[tuple[datetime, int, int, Turn]] = []
    for event in events:
        turn = Turn(speaker=SpeakerRole.MATCH_FEED, text=render_event(event.event))
        items.append((event.created_at, _KIND_EVENT, event.id, turn))
    for message in messages:
        speaker = (
            SpeakerRole.THIS_COMMENTATOR
            if message.commentator_id == perspective_commentator_id
            else SpeakerRole.CO_COMMENTATOR
        )
        turn = Turn(speaker=speaker, text=message.text)
        items.append((message.created_at, _KIND_MESSAGE, message.id, turn))
    items.sort(key=lambda item: (item[0], item[1], item[2]))
    return [turn for _, _, _, turn in items]
