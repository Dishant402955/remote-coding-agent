"""Environment-based configuration and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when required configuration is missing or invalid."""


def _positive_int(name: str, raw_value: str | None, default: int) -> int:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime settings. Secrets are never loaded from a committed file."""

    auth_token: str
    host: str = "127.0.0.1"
    port: int = 8000
    workspace: Path = Path("./workspace")
    default_timeout_seconds: int = 30
    max_timeout_seconds: int = 300
    max_output_chars: int = 20_000

    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings from environment variables, failing closed without a token."""
        token = os.getenv("MCP_AUTH_TOKEN", "").strip()
        if not token or token == "replace-with-a-long-random-secret":
            raise ConfigurationError(
                "MCP_AUTH_TOKEN must be set to a real secret before the server can start"
            )

        host = os.getenv("REMOTE_CODING_AGENT_HOST", "127.0.0.1").strip()
        if not host:
            raise ConfigurationError("REMOTE_CODING_AGENT_HOST must not be empty")

        port = _positive_int("REMOTE_CODING_AGENT_PORT", os.getenv("REMOTE_CODING_AGENT_PORT"), 8000)
        if port > 65535:
            raise ConfigurationError("REMOTE_CODING_AGENT_PORT must be at most 65535")

        default_timeout = _positive_int(
            "REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS",
            os.getenv("REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS"),
            30,
        )
        max_timeout = _positive_int(
            "REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS",
            os.getenv("REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS"),
            300,
        )
        if default_timeout > max_timeout:
            raise ConfigurationError(
                "REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS cannot exceed the maximum"
            )

        max_output = _positive_int(
            "REMOTE_CODING_AGENT_MAX_OUTPUT_CHARS",
            os.getenv("REMOTE_CODING_AGENT_MAX_OUTPUT_CHARS"),
            20_000,
        )
        workspace = Path(os.getenv("REMOTE_CODING_AGENT_WORKSPACE", "./workspace")).expanduser()

        return cls(
            auth_token=token,
            host=host,
            port=port,
            workspace=workspace,
            default_timeout_seconds=default_timeout,
            max_timeout_seconds=max_timeout,
            max_output_chars=max_output,
        )
