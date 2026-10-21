"""Tests for fail-closed environment configuration."""

import pytest

from remote_coding_agent.config import ConfigurationError, Settings


def test_missing_auth_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(ConfigurationError, match="MCP_AUTH_TOKEN"):
        Settings.from_env()


def test_placeholder_auth_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "replace-with-a-long-random-secret")
    with pytest.raises(ConfigurationError, match="real secret"):
        Settings.from_env()


def test_valid_environment_uses_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "a-test-secret-that-is-not-for-production")
    for name in (
        "REMOTE_CODING_AGENT_HOST",
        "REMOTE_CODING_AGENT_PORT",
        "REMOTE_CODING_AGENT_WORKSPACE",
        "REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS",
        "REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS",
        "REMOTE_CODING_AGENT_MAX_OUTPUT_CHARS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.default_timeout_seconds == 30
    assert settings.max_timeout_seconds == 300
    assert settings.max_output_chars == 20_000


@pytest.mark.parametrize("port", ["0", "-1", "65536", "not-a-port"])
def test_invalid_port_is_rejected(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "a-test-secret-that-is-not-for-production")
    monkeypatch.setenv("REMOTE_CODING_AGENT_PORT", port)
    with pytest.raises(ConfigurationError):
        Settings.from_env()


def test_default_timeout_cannot_exceed_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "a-test-secret-that-is-not-for-production")
    monkeypatch.setenv("REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS", "60")
    with pytest.raises(ConfigurationError, match="cannot exceed"):
        Settings.from_env()
