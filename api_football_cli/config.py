"""Explicit configuration loading.

Every value is read from a named environment variable and validated up front.
There are no fallbacks and no hidden defaults: a missing or invalid value
raises :class:`ConfigError` with the exact variable name that must be set.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Documented public endpoint of api-football v3. A known constant, not a guess;
# tests construct adapters with their own base URL explicitly.
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"

ENV_DATABASE_URL = "AFC_DATABASE_URL"
ENV_APIFOOTBALL_KEY = "AFC_APIFOOTBALL_KEY"
ENV_MODEL_PROVIDER = "AFC_MODEL_PROVIDER"
ENV_ANTHROPIC_API_KEY = "AFC_ANTHROPIC_API_KEY"
ENV_ANTHROPIC_MODEL = "AFC_ANTHROPIC_MODEL"
ENV_ANTHROPIC_MAX_TOKENS = "AFC_ANTHROPIC_MAX_TOKENS"

ASYNC_POSTGRES_PREFIX = "postgresql+asyncpg://"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        raise ConfigError(f"environment variable {name} must be set")
    return value


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str

    def require_postgres(self) -> None:
        if not self.url.startswith(ASYNC_POSTGRES_PREFIX):
            raise ConfigError(
                f"{ENV_DATABASE_URL} must start with {ASYNC_POSTGRES_PREFIX!r} "
                f"for the live runtime (LISTEN/NOTIFY needs Postgres), got {self.url!r}"
            )

    def notify_dsn(self) -> str:
        """Plain asyncpg DSN for the LISTEN/NOTIFY connection."""
        self.require_postgres()
        return "postgresql://" + self.url.removeprefix(ASYNC_POSTGRES_PREFIX)


class ApiFootballConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    base_url: str


class AnthropicConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_key: str
    model: str
    max_tokens: int


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Literal["anthropic", "fake"]
    anthropic: AnthropicConfig | None


def load_database_config() -> DatabaseConfig:
    return DatabaseConfig(url=require_env(ENV_DATABASE_URL))


def load_apifootball_config() -> ApiFootballConfig:
    return ApiFootballConfig(key=require_env(ENV_APIFOOTBALL_KEY), base_url=API_FOOTBALL_BASE_URL)


def load_model_config() -> ModelConfig:
    provider = require_env(ENV_MODEL_PROVIDER)
    if provider == "fake":
        return ModelConfig(provider="fake", anthropic=None)
    if provider == "anthropic":
        raw_max_tokens = require_env(ENV_ANTHROPIC_MAX_TOKENS)
        try:
            max_tokens = int(raw_max_tokens)
        except ValueError as exc:
            raise ConfigError(
                f"{ENV_ANTHROPIC_MAX_TOKENS} must be an integer, got {raw_max_tokens!r}"
            ) from exc
        if max_tokens <= 0:
            raise ConfigError(f"{ENV_ANTHROPIC_MAX_TOKENS} must be positive, got {max_tokens}")
        return ModelConfig(
            provider="anthropic",
            anthropic=AnthropicConfig(
                api_key=require_env(ENV_ANTHROPIC_API_KEY),
                model=require_env(ENV_ANTHROPIC_MODEL),
                max_tokens=max_tokens,
            ),
        )
    raise ConfigError(
        f"{ENV_MODEL_PROVIDER} must be one of 'anthropic', 'fake'; got {provider!r}"
    )
