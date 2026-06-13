"""Director turn-taking policy."""

from __future__ import annotations

import pytest

from api_football_cli.domain.director import DirectorError, plan_round
from api_football_cli.domain.entities import CommentatorRole
from tests.factories import BOOTH, make_commentator


def test_round_is_play_by_play_then_color() -> None:
    speakers = plan_round(commentators=BOOTH, max_messages=2)
    assert [s.role for s in speakers] == [CommentatorRole.PLAY_BY_PLAY, CommentatorRole.COLOR]


def test_round_caps_at_max_messages() -> None:
    speakers = plan_round(commentators=BOOTH, max_messages=1)
    assert [s.role for s in speakers] == [CommentatorRole.PLAY_BY_PLAY]


def test_round_rejects_non_positive_cap() -> None:
    with pytest.raises(DirectorError, match="max_messages"):
        plan_round(commentators=BOOTH, max_messages=0)


def test_round_requires_both_roles() -> None:
    only_pbp = (make_commentator(id=1),)
    with pytest.raises(DirectorError, match="color"):
        plan_round(commentators=only_pbp, max_messages=2)


def test_round_rejects_duplicate_roles() -> None:
    booth = (
        make_commentator(id=1),
        make_commentator(id=2, name="Second Voice"),
        make_commentator(id=3, role=CommentatorRole.COLOR, name="Robbie Banks"),
    )
    with pytest.raises(DirectorError, match="play_by_play"):
        plan_round(commentators=booth, max_messages=2)
