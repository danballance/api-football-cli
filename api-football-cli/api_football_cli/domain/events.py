"""Event identity (content hash) and feed-line rendering.

The hash deliberately covers (elapsed, extra, team, player, type, detail) and
deliberately excludes assist and comments: late assist attributions must not
create phantom duplicate events (architecture §2). VAR corrections change
detail/type, hash differently, and are therefore appended as new events.
"""

from __future__ import annotations

import hashlib

from api_football_cli.domain.entities import ObservedEvent

_GOAL_DETAILS_SCORING = frozenset({"Normal Goal", "Own Goal", "Penalty"})


def event_hash(event: ObservedEvent) -> str:
    player_id = event.player.api_player_id if event.player is not None else None
    parts = (
        event.elapsed,
        event.extra,
        event.team.api_team_id,
        player_id,
        event.type,
        event.detail,
    )
    canonical = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def is_scoring_goal(event: ObservedEvent) -> bool:
    """True when the event adds a goal to the score of ``event.team``."""
    return event.type == "Goal" and event.detail in _GOAL_DETAILS_SCORING


def minute_label(event: ObservedEvent) -> str:
    if event.extra:
        return f"{event.elapsed}+{event.extra}'"
    return f"{event.elapsed}'"


def _player_name(event: ObservedEvent) -> str:
    if event.player is not None and event.player.name:
        return event.player.name
    return "unknown player"


def render_event(event: ObservedEvent) -> str:
    """Render one feed event as a single human-readable line."""
    minute = minute_label(event)
    team = event.team.name
    player = _player_name(event)

    if event.type == "Goal":
        if event.detail == "Missed Penalty":
            line = f"{minute} MISSED PENALTY — {team}: {player} fails to convert"
        elif event.detail.startswith("Goal Disallowed"):
            line = f"{minute} GOAL DISALLOWED — {team}: {player} ({event.detail})"
        else:
            line = f"{minute} GOAL — {team}: {player} ({event.detail})"
            if event.assist is not None and event.assist.name:
                line += f", assist: {event.assist.name}"
    elif event.type == "Card":
        line = f"{minute} {event.detail.upper()} — {team}: {player}"
    elif event.type == "subst":
        if event.assist is not None and event.assist.name:
            line = f"{minute} SUBSTITUTION — {team}: {player} replaces {event.assist.name}"
        else:
            line = f"{minute} SUBSTITUTION — {team}: {player} comes on"
    elif event.type == "Var":
        line = f"{minute} VAR — {team}: {event.detail}"
    else:
        line = f"{minute} {event.type} — {team}: {event.detail}"

    if event.comments:
        line += f" [{event.comments}]"
    return line
