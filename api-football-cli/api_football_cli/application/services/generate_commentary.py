"""GenerateCommentaryRound + the long-lived worker (architecture §7).

Each notification triggers a *round*, not a single message: new events since
the last commented event are coalesced, the director orders the speakers, and
every speaker gets one model call over the shared booth transcript. A
per-service asyncio lock guarantees one round at a time per fixture.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from api_football_cli.application.ports.commentary_model import CommentaryModel
from api_football_cli.application.ports.event_bus import (
    FIXTURE_EVENTS_CHANNEL,
    EventBus,
)
from api_football_cli.application.ports.repositories import (
    CommentaryRepository,
    EventRepository,
    FixtureRepository,
)
from api_football_cli.domain.director import plan_round
from api_football_cli.domain.entities import (
    CommentaryDraft,
    CommentaryMessage,
    Commentator,
    Fixture,
)
from api_football_cli.domain.personas import match_system_prompt
from api_football_cli.domain.transcript import build_transcript


class GenerateCommentaryRound:
    def __init__(
        self,
        *,
        fixtures: FixtureRepository,
        events: EventRepository,
        commentary: CommentaryRepository,
        model: CommentaryModel,
        commentators: Sequence[Commentator],
        max_messages_per_round: int,
    ) -> None:
        self._fixtures = fixtures
        self._events = events
        self._commentary = commentary
        self._model = model
        self._commentators = tuple(commentators)
        self._max_messages_per_round = max_messages_per_round
        self._lock = asyncio.Lock()

    async def run(self, fixture_id: int) -> list[CommentaryMessage]:
        async with self._lock:
            last_commented = await self._commentary.last_triggering_event_id(fixture_id)
            new_events = await self._events.list_after(
                fixture_id=fixture_id, after_event_id=last_commented
            )
            if not new_events:
                return []

            fixture = await self._fixtures.get(fixture_id)
            all_events = await self._events.list_for_fixture(fixture_id)
            history = await self._commentary.list_for_fixture(fixture_id)
            speakers = plan_round(
                commentators=self._commentators, max_messages=self._max_messages_per_round
            )
            triggering_event_id = max(event.id for event in new_events)

            produced: list[CommentaryMessage] = []
            for speaker in speakers:
                transcript = build_transcript(
                    events=all_events,
                    messages=history,
                    perspective_commentator_id=speaker.id,
                )
                result = await self._model.generate(
                    system=_system_prompt(speaker, fixture), transcript=transcript
                )
                draft = CommentaryDraft(
                    fixture_id=fixture_id,
                    commentator_id=speaker.id,
                    text=result.text.strip(),
                    triggering_event_id=triggering_event_id,
                    in_reply_to=produced[-1].id if produced else None,
                    provider=self._model.provider,
                    model=self._model.model_id,
                    usage=result.usage,
                )
                message = await self._commentary.insert(draft)
                history.append(message)
                produced.append(message)
            return produced


def _system_prompt(speaker: Commentator, fixture: Fixture) -> str:
    return match_system_prompt(
        persona_prompt=speaker.system_prompt,
        home=fixture.home.name,
        away=fixture.away.name,
        league=fixture.league.name,
        season=fixture.league.season,
    )


class CommentaryWorker:
    """Subscribes to fixture_event_inserted and runs rounds for one fixture."""

    def __init__(
        self, *, bus: EventBus, rounds: GenerateCommentaryRound, fixture_id: int
    ) -> None:
        self._bus = bus
        self._rounds = rounds
        self._fixture_id = fixture_id

    async def run(self) -> None:
        async with self._bus.subscribe(FIXTURE_EVENTS_CHANNEL) as notifications:
            # Catch-up round: events inserted before our LISTEN registered are
            # picked up by a select, not by a notification (architecture §5).
            await self._rounds.run(self._fixture_id)
            async for notification in notifications:
                if notification.fixture_id != self._fixture_id:
                    continue
                await self._rounds.run(self._fixture_id)
