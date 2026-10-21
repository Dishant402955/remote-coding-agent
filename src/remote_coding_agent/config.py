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
    max_command_chars: int = 16_384
    max_concurrent_commands: int = 4
    max_request_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        token = self.auth_token.strip() if isinstance(self.auth_token, str) else ""
        if len(token) < 32 or token == "replace-with-a-long-random-secret":
            raise ConfigurationError(
                "MCP_AUTH_TOKEN must contain at least 32 characters of secret material"
            )
        if not isinstance(self.host, str) or not self.host.strip():
            raise ConfigurationError("REMOTE_CODING_AGENT_HOST must not be empty")
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise ConfigurationError("REMOTE_CODING_AGENT_PORT must be an integer")
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("REMOTE_CODING_AGENT_PORT must be between 1 and 65535")
        for name, value in (
            ("default_timeout_seconds", self.default_timeout_seconds),
            ("max_timeout_seconds", self.max_timeout_seconds),
            ("max_output_chars", self.max_output_chars),
            ("max_command_chars", self.max_command_chars),
            ("max_concurrent_commands", self.max_concurrent_commands),
            ("max_request_bytes", self.max_request_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ConfigurationError(f"{name} must be an integer")
        if self.default_timeout_seconds < 1 or self.max_timeout_seconds < 1:
            raise ConfigurationError("command timeouts must be greater than zero")
        if self.max_timeout_seconds > 3600:
            raise ConfigurationError("maximum command timeout cannot exceed 3600 seconds")
        if self.default_timeout_seconds > self.max_timeout_seconds:
            raise ConfigurationError(
                "REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS cannot exceed the maximum"
            )
        if not 1 <= self.max_output_chars <= 1_000_000:
            raise ConfigurationError("max_output_chars must be between 1 and 1000000")
        if not 1 <= self.max_command_chars <= 65_536:
            raise ConfigurationError("max_command_chars must be between 1 and 65536")
        if not 1 <= self.max_concurrent_commands <= 16:
            raise ConfigurationError("max_concurrent_commands must be between 1 and 16")
        if not 1 <= self.max_request_bytes <= 10_485_760:
            raise ConfigurationError("max_request_bytes must be between 1 and 10485760")
        object.__setattr__(self, "auth_token", token)
        object.__setattr__(self, "workspace", Path(self.workspace).expanduser())

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from environment variables, failing closed without a token."""
        token = os.getenv("MCP_AUTH_TOKEN", "").strip()
        if not token:
            raise ConfigurationError("MCP_AUTH_TOKEN must be set before the server can start")

        host = os.getenv("REMOTE_CODING_AGENT_HOST", "127.0.0.1").strip()
        port = _positive_int(
            "REMOTE_CODING_AGENT_PORT",
            os.getenv("REMOTE_CODING_AGENT_PORT"),
            8000,
        )
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
        max_output = _positive_int(
            "REMOTE_CODING_AGENT_MAX_OUTPUT_CHARS",
            os.getenv("REMOTE_CODING_AGENT_MAX_OUTPUT_CHARS"),
            20_000,
        )
        max_command = _positive_int(
            "REMOTE_CODING_AGENT_MAX_COMMAND_CHARS",
            os.getenv("REMOTE_CODING_AGENT_MAX_COMMAND_CHARS"),
            16_384,
        )
        max_concurrent = _positive_int(
            "REMOTE_CODING_AGENT_MAX_CONCURRENT_COMMANDS",
            os.getenv("REMOTE_CODING_AGENT_MAX_CONCURRENT_COMMANDS"),
            4,
        )
        max_request_bytes = _positive_int(
            "REMOTE_CODING_AGENT_MAX_REQUEST_BYTES",
            os.getenv("REMOTE_CODING_AGENT_MAX_REQUEST_BYTES"),
            1_048_576,
        )
        workspace = Path(
            os.getenv("REMOTE_CODING_AGENT_WORKSPACE", "./workspace")
        ).expanduser()

        return cls(
            auth_token=token,
            host=host,
            port=port,
            workspace=workspace,
            default_timeout_seconds=default_timeout,
            max_timeout_seconds=max_timeout,
            max_output_chars=max_output,
            max_command_chars=max_command,
            max_concurrent_commands=max_concurrent,
            max_request_bytes=max_request_bytes,
        )
