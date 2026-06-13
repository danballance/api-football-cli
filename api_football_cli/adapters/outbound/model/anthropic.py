"""AnthropicCommentaryModel: CommentaryModel adapter over the Anthropic SDK.

Provider specifics stay inside this module (architecture §8):

- Speaker tags map onto Messages API roles: this_commentator -> assistant,
  co_commentator / match_feed -> labelled user turns.
- The persona system prompt is frozen per match and marked with
  cache_control so the prefix is cached; the growing transcript is cached via
  the top-level auto breakpoint on the last message.
- The transcript must end with a user turn: assistant prefills are not
  supported by current Claude models, and the director guarantees this.
"""

from __future__ import annotations

from collections.abc import Sequence

import anthropic
from anthropic.types import MessageParam

from api_football_cli.application.ports.commentary_model import (
    CommentaryModel,
    CommentaryModelError,
)
from api_football_cli.domain.entities import CommentaryResult, SpeakerRole, Turn

_USER_LABELS = {
    SpeakerRole.CO_COMMENTATOR: "[CO-COMMENTATOR]",
    SpeakerRole.MATCH_FEED: "[MATCH FEED]",
}


def transcript_to_messages(transcript: Sequence[Turn]) -> list[MessageParam]:
    """Map booth turns onto API messages, merging consecutive same-role turns."""
    if not transcript:
        raise CommentaryModelError("transcript must not be empty")

    merged: list[tuple[str, list[str]]] = []
    for turn in transcript:
        if turn.speaker is SpeakerRole.THIS_COMMENTATOR:
            role, text = "assistant", turn.text
        else:
            role, text = "user", f"{_USER_LABELS[turn.speaker]} {turn.text}"
        if merged and merged[-1][0] == role:
            merged[-1][1].append(text)
        else:
            merged.append((role, [text]))

    if merged[0][0] != "user":
        raise CommentaryModelError("transcript must start with a feed or colleague turn")
    if merged[-1][0] != "user":
        raise CommentaryModelError(
            "transcript must end with a user turn (assistant prefill is unsupported)"
        )
    return [
        MessageParam(role="user" if role == "user" else "assistant", content="\n".join(texts))
        for role, texts in merged
    ]


class AnthropicCommentaryModel(CommentaryModel):
    def __init__(self, *, client: anthropic.AsyncAnthropic, model: str, max_tokens: int) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    @property
    def provider(self) -> str:
        return "anthropic"

    @property
    def model_id(self) -> str:
        return self._model

    async def generate(self, *, system: str, transcript: Sequence[Turn]) -> CommentaryResult:
        messages = transcript_to_messages(transcript)
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                cache_control={"type": "ephemeral"},
                messages=messages,
            )
        except anthropic.APIError as exc:
            raise CommentaryModelError(f"anthropic request failed: {exc}") from exc

        if response.stop_reason == "refusal":
            raise CommentaryModelError("anthropic declined to generate this line (refusal)")

        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not text:
            raise CommentaryModelError(
                f"anthropic returned no text (stop_reason={response.stop_reason!r})"
            )

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        if response.usage.cache_read_input_tokens is not None:
            usage["cache_read_input_tokens"] = response.usage.cache_read_input_tokens
        if response.usage.cache_creation_input_tokens is not None:
            usage["cache_creation_input_tokens"] = response.usage.cache_creation_input_tokens
        return CommentaryResult(text=text, usage=usage)
