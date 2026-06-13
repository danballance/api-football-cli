"""FakeModel: deterministic CommentaryModel for tests and offline demos."""

from __future__ import annotations

from collections.abc import Sequence

from api_football_cli.application.ports.commentary_model import CommentaryModelError
from api_football_cli.domain.entities import CommentaryResult, SpeakerRole, Turn

# Lines for demo runs; "{feed}" is replaced with the latest match-feed line so
# the fake stays visibly reactive without any model spend.
DEFAULT_FAKE_LINES: tuple[str, ...] = (
    "And there it is — {feed}!",
    "You simply cannot defend like that at this level, look again at {feed}.",
    "The feed confirms it: {feed}. This crowd is on its feet.",
    "I said it before kickoff and I will say it again — moments like {feed} decide matches.",
)


class FakeModel:
    def __init__(self, *, lines: Sequence[str]) -> None:
        if not lines:
            raise CommentaryModelError("FakeModel needs at least one canned line")
        self._lines = tuple(lines)
        self._call_count = 0
        self.calls: list[tuple[str, tuple[Turn, ...]]] = []

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return "fake-commentator"

    async def generate(self, *, system: str, transcript: Sequence[Turn]) -> CommentaryResult:
        if not transcript:
            raise CommentaryModelError("transcript must not be empty")
        self.calls.append((system, tuple(transcript)))
        line = self._lines[self._call_count % len(self._lines)]
        self._call_count += 1
        latest_feed = next(
            (turn.text for turn in reversed(transcript) if turn.speaker is SpeakerRole.MATCH_FEED),
            "the action",
        )
        return CommentaryResult(text=line.format(feed=latest_feed), usage=None)
