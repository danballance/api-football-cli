"""Event identity hash and feed-line rendering."""

from __future__ import annotations

from api_football_cli.domain.entities import PlayerRef
from api_football_cli.domain.events import (
    event_hash,
    is_scoring_goal,
    minute_label,
    render_event,
)
from tests.factories import AWAY, make_event


def test_hash_is_stable_for_identical_content() -> None:
    assert event_hash(make_event()) == event_hash(make_event())


def test_hash_ignores_assist_and_comments() -> None:
    base = make_event()
    with_assist = make_event(
        assist=PlayerRef(api_player_id=70002, name="L. Carter"), comments="header"
    )
    assert event_hash(base) == event_hash(with_assist)


def test_hash_changes_when_detail_changes() -> None:
    goal = make_event(detail="Normal Goal")
    disallowed = make_event(detail="Goal Disallowed - offside")
    assert event_hash(goal) != event_hash(disallowed)


def test_hash_changes_per_minute_team_player_and_type() -> None:
    base = make_event()
    assert event_hash(base) != event_hash(make_event(elapsed=11))
    assert event_hash(base) != event_hash(make_event(extra=2))
    assert event_hash(base) != event_hash(make_event(team=AWAY))
    assert event_hash(base) != event_hash(
        make_event(player=PlayerRef(api_player_id=70099, name="Other"))
    )
    assert event_hash(base) != event_hash(make_event(type_="Card", detail="Yellow Card"))


def test_hash_handles_missing_player() -> None:
    anonymous = make_event(player=None)
    named = make_event()
    assert event_hash(anonymous) != event_hash(named)
    assert event_hash(anonymous) == event_hash(make_event(player=None))


def test_minute_label_with_and_without_extra() -> None:
    assert minute_label(make_event(elapsed=90, extra=3)) == "90+3'"
    assert minute_label(make_event(elapsed=12)) == "12'"


def test_is_scoring_goal() -> None:
    assert is_scoring_goal(make_event(detail="Normal Goal"))
    assert is_scoring_goal(make_event(detail="Own Goal"))
    assert is_scoring_goal(make_event(detail="Penalty"))
    assert not is_scoring_goal(make_event(detail="Missed Penalty"))
    assert not is_scoring_goal(make_event(type_="Card", detail="Yellow Card"))


def test_render_goal_with_assist_and_comment() -> None:
    line = render_event(
        make_event(assist=PlayerRef(api_player_id=70002, name="L. Carter"), comments="header")
    )
    assert line == "10' GOAL — Riverton Albion: D. Mercer (Normal Goal), assist: L. Carter [header]"


def test_render_missed_penalty() -> None:
    line = render_event(make_event(detail="Missed Penalty"))
    assert "MISSED PENALTY" in line


def test_render_disallowed_goal() -> None:
    line = render_event(make_event(detail="Goal Disallowed - offside"))
    assert line.startswith("10' GOAL DISALLOWED — Riverton Albion: D. Mercer")


def test_render_card_substitution_var_and_unknown() -> None:
    card = render_event(make_event(type_="Card", detail="Red Card"))
    assert "RED CARD" in card

    sub = render_event(
        make_event(
            type_="subst",
            detail="Substitution 1",
            assist=PlayerRef(api_player_id=70006, name="E. Mason"),
        )
    )
    assert "SUBSTITUTION" in sub and "replaces E. Mason" in sub

    sub_no_off = render_event(make_event(type_="subst", detail="Substitution 2"))
    assert sub_no_off.endswith("comes on")

    var = render_event(make_event(type_="Var", detail="Goal cancelled"))
    assert "VAR" in var and "Goal cancelled" in var

    unknown = render_event(make_event(type_="Weather", detail="Heavy rain"))
    assert "Weather — Riverton Albion: Heavy rain" in unknown


def test_render_anonymous_player() -> None:
    line = render_event(make_event(player=None))
    assert "unknown player" in line
