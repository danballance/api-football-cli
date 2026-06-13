"""The director: deterministic turn-taking policy for a commentary round.

A round is triggered by new match events; the director decides who speaks and
in which order. The starting policy (architecture §7) is fixed and simple:
play-by-play reacts first, the color commentator responds, capped by an
explicit message budget so a round can never run away.
"""

from __future__ import annotations

from collections.abc import Sequence

from api_football_cli.domain.entities import Commentator, CommentatorRole


class DirectorError(RuntimeError):
    """Raised when the booth is not staffed correctly."""


def plan_round(
    *, commentators: Sequence[Commentator], max_messages: int
) -> list[Commentator]:
    """Return the ordered speakers for one round."""
    if max_messages < 1:
        raise DirectorError(f"max_messages must be >= 1, got {max_messages}")

    play_by_play = _single_with_role(commentators, CommentatorRole.PLAY_BY_PLAY)
    color = _single_with_role(commentators, CommentatorRole.COLOR)
    return [play_by_play, color][:max_messages]


def _single_with_role(
    commentators: Sequence[Commentator], role: CommentatorRole
) -> Commentator:
    matching = [c for c in commentators if c.role is role]
    if len(matching) != 1:
        raise DirectorError(
            f"expected exactly one commentator with role {role.value!r}, found {len(matching)}"
        )
    return matching[0]
