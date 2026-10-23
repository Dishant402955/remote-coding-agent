"""Tests for fail-closed environment configuration."""

import pytest

from remote_coding_agent.config import ConfigurationError, Settings

TEST_TOKEN = "a-test-secret-that-is-long-enough-for-settings"


def test_missing_auth_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(ConfigurationError, match="MCP_AUTH_TOKEN"):
        Settings.from_env()


def test_placeholder_auth_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "replace-with-a-long-random-secret")
    with pytest.raises(ConfigurationError, match="at least 32"):
        Settings.from_env()


def test_short_auth_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "short")
    with pytest.raises(ConfigurationError, match="at least 32"):
        Settings.from_env()


def test_valid_environment_uses_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", TEST_TOKEN)
    for name in (
        "REMOTE_CODING_AGENT_HOST",
        "REMOTE_CODING_AGENT_PORT",
        "REMOTE_CODING_AGENT_WORKSPACE",
        "REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS",
        "REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS",
        "REMOTE_CODING_AGENT_MAX_OUTPUT_CHARS",
        "REMOTE_CODING_AGENT_MAX_COMMAND_CHARS",
        "REMOTE_CODING_AGENT_MAX_CONCURRENT_COMMANDS",
        "REMOTE_CODING_AGENT_MAX_REQUEST_BYTES",
        "REMOTE_CODING_AGENT_TEST_MODE",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.default_timeout_seconds == 30
    assert settings.max_timeout_seconds == 300
    assert settings.max_output_chars == 20_000
    assert settings.max_command_chars == 16_384
    assert settings.max_concurrent_commands == 4
    assert settings.max_request_bytes == 1_048_576
    assert settings.test_mode is False


@pytest.mark.parametrize("port", ["0", "-1", "65536", "not-a-port"])
def test_invalid_port_is_rejected(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("REMOTE_CODING_AGENT_PORT", port)
    with pytest.raises(ConfigurationError):
        Settings.from_env()


def test_default_timeout_cannot_exceed_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS", "60")
    with pytest.raises(ConfigurationError, match="cannot exceed"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_output_chars", 1_000_001),
        ("max_command_chars", 65_537),
        ("max_concurrent_commands", 17),
        ("max_timeout_seconds", 3601),
        ("max_request_bytes", 10_485_761),
    ],
)
def test_direct_settings_construction_is_validated(field, value) -> None:
    with pytest.raises(ConfigurationError):
        Settings(**{"auth_token": TEST_TOKEN, field: value})


def test_direct_settings_rejects_non_integer_limits() -> None:
    with pytest.raises(ConfigurationError, match="max_output_chars must be an integer"):
        Settings(auth_token=TEST_TOKEN, max_output_chars=10.5)


def test_direct_settings_rejects_non_string_host() -> None:
    with pytest.raises(ConfigurationError, match="HOST must not be empty"):
        Settings(auth_token=TEST_TOKEN, host=123)



def test_no_auth_mode_does_not_require_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("REMOTE_CODING_AGENT_AUTH_MODE", "none")

    settings = Settings.from_env()

    assert settings.auth_mode == "none"
    assert settings.auth_token == ""



def test_test_mode_environment_flag_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTE_CODING_AGENT_AUTH_MODE", "none")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("REMOTE_CODING_AGENT_TEST_MODE", "true")

    settings = Settings.from_env()

    assert settings.test_mode is True


def test_invalid_test_mode_environment_flag_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("REMOTE_CODING_AGENT_TEST_MODE", "sometimes")

    with pytest.raises(ConfigurationError, match="TEST_MODE"):
        Settings.from_env()
