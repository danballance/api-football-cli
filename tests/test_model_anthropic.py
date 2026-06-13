"""Anthropic adapter: role mapping, caching markers, usage, failure modes."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient
from api_football_cli.adapters.outbound.model.anthropic import (
    AnthropicCommentaryModel,
    transcript_to_messages,
)
from api_football_cli.application.ports.commentary_model import CommentaryModelError
from api_football_cli.domain.entities import SpeakerRole, Turn

FEED = Turn(speaker=SpeakerRole.MATCH_FEED, text="9' GOAL — Riverton: D. Mercer")
MINE = Turn(speaker=SpeakerRole.THIS_COMMENTATOR, text="Mercer makes it one nil!")
COLLEAGUE = Turn(speaker=SpeakerRole.CO_COMMENTATOR, text="Lovely first touch, Marty.")


def test_transcript_mapping_merges_and_labels() -> None:
    messages = transcript_to_messages((FEED, COLLEAGUE, MINE, FEED))
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    first_content = messages[0]["content"]
    assert isinstance(first_content, str)
    assert first_content.splitlines() == [
        "[MATCH FEED] 9' GOAL — Riverton: D. Mercer",
        "[CO-COMMENTATOR] Lovely first touch, Marty.",
    ]
    assert messages[1]["content"] == "Mercer makes it one nil!"


def test_transcript_mapping_rejects_bad_shapes() -> None:
    with pytest.raises(CommentaryModelError, match="empty"):
        transcript_to_messages(())
    with pytest.raises(CommentaryModelError, match="start"):
        transcript_to_messages((MINE, FEED))
    with pytest.raises(CommentaryModelError, match="end with a user turn"):
        transcript_to_messages((FEED, MINE))


def _client(handler: httpx.MockTransport) -> AsyncAnthropic:
    return AsyncAnthropic(
        api_key="sk-test", http_client=DefaultAsyncHttpxClient(transport=handler)
    )


def _model(handler: httpx.MockTransport) -> AnthropicCommentaryModel:
    return AnthropicCommentaryModel(
        client=_client(handler), model="claude-opus-4-8", max_tokens=200
    )


def _message_json(text: str, stop_reason: str) -> dict[str, Any]:
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-8",
        "content": [] if text == "" else [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 14,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 90,
        },
    }


async def test_generate_sends_cached_prompt_and_returns_usage() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_message_json("  And it's one nil! ", "end_turn"))

    result = await _model(httpx.MockTransport(handler)).generate(
        system="PERSONA PROMPT", transcript=(FEED, COLLEAGUE)
    )

    assert captured["model"] == "claude-opus-4-8"
    assert captured["max_tokens"] == 200
    assert captured["system"] == [
        {"type": "text", "text": "PERSONA PROMPT", "cache_control": {"type": "ephemeral"}}
    ]
    assert captured["cache_control"] == {"type": "ephemeral"}
    assert [m["role"] for m in captured["messages"]] == ["user"]

    assert result.text == "And it's one nil!"
    assert result.usage == {
        "input_tokens": 120,
        "output_tokens": 14,
        "cache_read_input_tokens": 90,
        "cache_creation_input_tokens": 0,
    }


async def test_generate_raises_on_refusal() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=_message_json("", "refusal"))
    )
    with pytest.raises(CommentaryModelError, match="refusal"):
        await _model(transport).generate(system="sys", transcript=(FEED,))


async def test_generate_raises_on_empty_text() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=_message_json("", "end_turn"))
    )
    with pytest.raises(CommentaryModelError, match="no text"):
        await _model(transport).generate(system="sys", transcript=(FEED,))


async def test_generate_wraps_api_errors() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            400,
            json={
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "broken request"},
            },
        )
    )
    with pytest.raises(CommentaryModelError, match="anthropic request failed"):
        await _model(transport).generate(system="sys", transcript=(FEED,))


def test_identity() -> None:
    model = _model(httpx.MockTransport(lambda request: httpx.Response(500)))
    assert model.provider == "anthropic"
    assert model.model_id == "claude-opus-4-8"
