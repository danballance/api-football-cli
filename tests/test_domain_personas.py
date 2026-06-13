"""Persona seeds and the frozen per-match system prompt."""

from __future__ import annotations

from api_football_cli.domain.entities import CommentatorRole
from api_football_cli.domain.personas import PERSONAS, match_system_prompt


def test_personas_cover_both_roles() -> None:
    roles = {persona.role for persona in PERSONAS}
    assert roles == {CommentatorRole.PLAY_BY_PLAY, CommentatorRole.COLOR}


def test_persona_prompts_set_the_ground_rules() -> None:
    for persona in PERSONAS:
        assert "MATCH FEED" in persona.system_prompt
        assert persona.name.split()[0] in persona.system_prompt


def test_match_system_prompt_is_stable_and_score_free() -> None:
    prompt_a = match_system_prompt(
        persona_prompt="PERSONA", home="Riverton", away="Kingsport", league="DPL", season=2025
    )
    prompt_b = match_system_prompt(
        persona_prompt="PERSONA", home="Riverton", away="Kingsport", league="DPL", season=2025
    )
    assert prompt_a == prompt_b
    assert prompt_a.startswith("PERSONA")
    assert "Riverton vs Kingsport" in prompt_a
    assert "2025" in prompt_a
