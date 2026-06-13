"""The provider-neutral CommentaryModel port (architecture §8)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from api_football_cli.domain.entities import CommentaryResult, Turn


class CommentaryModelError(RuntimeError):
    """Any model-provider failure, surfaced loudly (fail fast, no retries)."""


class CommentaryModel(Protocol):
    @property
    def provider(self) -> str:
        """Short provider tag stored on each message (e.g. 'anthropic', 'fake')."""
        ...

    @property
    def model_id(self) -> str: ...

    async def generate(self, *, system: str, transcript: Sequence[Turn]) -> CommentaryResult:
        """Generate one complete spoken line for the booth transcript."""
        ...
