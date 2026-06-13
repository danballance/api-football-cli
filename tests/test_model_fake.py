"""FakeModel: deterministic, reactive, records its calls."""

from __future__ import annotations

import pytest
from api_football_cli.adapters.outbound.model.fake import DEFAULT_FAKE_LINES, FakeModel
from api_football_cli.application.ports.commentary_model import CommentaryModelError
from api_football_cli.domain.entities import SpeakerRole, Turn

TRANSCRIPT = (
    Turn(speaker=SpeakerRole.MATCH_FEED, text="9' GOAL — Riverton Albion: D. Mercer"),
    Turn(speaker=SpeakerRole.CO_COMMENTATOR, text="Tell me about that finish."),
)


async def test_lines_cycle_and_react_to_latest_feed() -> None:
    model = FakeModel(lines=("A: {feed}", "B"))
    first = await model.generate(system="sys", transcript=TRANSCRIPT)
    second = await model.generate(system="sys", transcript=TRANSCRIPT)
    third = await model.generate(system="sys", transcript=TRANSCRIPT)
    assert first.text == "A: 9' GOAL — Riverton Albion: D. Mercer"
    assert second.text == "B"
    assert third.text == first.text
    assert first.usage is None
    assert model.provider == "fake"
    assert model.model_id == "fake-commentator"
    assert len(model.calls) == 3


async def test_default_lines_are_usable() -> None:
    model = FakeModel(lines=DEFAULT_FAKE_LINES)
    result = await model.generate(system="sys", transcript=TRANSCRIPT)
    assert "9' GOAL" in result.text


async def test_transcript_must_not_be_empty() -> None:
    model = FakeModel(lines=("x",))
    with pytest.raises(CommentaryModelError, match="empty"):
        await model.generate(system="sys", transcript=())


def test_lines_must_not_be_empty() -> None:
    with pytest.raises(CommentaryModelError, match="at least one"):
        FakeModel(lines=())


async def test_no_feed_line_falls_back_to_generic_subject() -> None:
    model = FakeModel(lines=("React: {feed}",))
    result = await model.generate(
        system="sys",
        transcript=(Turn(speaker=SpeakerRole.CO_COMMENTATOR, text="Quiet spell."),),
    )
    assert result.text == "React: the action"
