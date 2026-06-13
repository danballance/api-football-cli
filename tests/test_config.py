"""Explicit configuration loading: every miss fails fast with the var name."""

from __future__ import annotations

import pytest
from api_football_cli.config import (
    API_FOOTBALL_BASE_URL,
    ConfigError,
    DatabaseConfig,
    load_apifootball_config,
    load_database_config,
    load_model_config,
    require_env,
)


def test_require_env_rejects_missing_and_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AFC_TEST_VAR", raising=False)
    with pytest.raises(ConfigError, match="AFC_TEST_VAR"):
        require_env("AFC_TEST_VAR")
    monkeypatch.setenv("AFC_TEST_VAR", "  ")
    with pytest.raises(ConfigError, match="AFC_TEST_VAR"):
        require_env("AFC_TEST_VAR")
    monkeypatch.setenv("AFC_TEST_VAR", "value")
    assert require_env("AFC_TEST_VAR") == "value"


def test_database_config_loads_and_derives_notify_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AFC_DATABASE_URL", "postgresql+asyncpg://app:pw@db:5432/afc")
    config = load_database_config()
    assert config.notify_dsn() == "postgresql://app:pw@db:5432/afc"


def test_database_config_rejects_non_postgres_for_runtime() -> None:
    config = DatabaseConfig(url="sqlite+aiosqlite:///x.db")
    with pytest.raises(ConfigError, match="postgresql\\+asyncpg"):
        config.require_postgres()


def test_apifootball_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFC_APIFOOTBALL_KEY", "secret")
    config = load_apifootball_config()
    assert config.key == "secret"
    assert config.base_url == API_FOOTBALL_BASE_URL


def test_model_config_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFC_MODEL_PROVIDER", "fake")
    config = load_model_config()
    assert config.provider == "fake"
    assert config.anthropic is None


def test_model_config_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFC_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("AFC_ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AFC_ANTHROPIC_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("AFC_ANTHROPIC_MAX_TOKENS", "300")
    config = load_model_config()
    assert config.provider == "anthropic"
    assert config.anthropic is not None
    assert config.anthropic.model == "claude-opus-4-8"
    assert config.anthropic.max_tokens == 300


def test_model_config_anthropic_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFC_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("AFC_ANTHROPIC_MAX_TOKENS", "300")
    monkeypatch.delenv("AFC_ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="AFC_ANTHROPIC_API_KEY"):
        load_model_config()


def test_model_config_rejects_bad_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFC_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("AFC_ANTHROPIC_MAX_TOKENS", "many")
    with pytest.raises(ConfigError, match="integer"):
        load_model_config()
    monkeypatch.setenv("AFC_ANTHROPIC_MAX_TOKENS", "0")
    with pytest.raises(ConfigError, match="positive"):
        load_model_config()


def test_model_config_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFC_MODEL_PROVIDER", "mystery")
    with pytest.raises(ConfigError, match="mystery"):
        load_model_config()
