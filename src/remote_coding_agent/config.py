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
    auth_token: str = ""
    auth_mode: str = "bearer"
    host: str = "127.0.0.1"
    port: int = 8000
    workspace: Path = Path("./workspace")
    default_timeout_seconds: int = 120
    max_timeout_seconds: int = 3600
    command_queue_timeout_seconds: int = 30
    session_idle_timeout_seconds: int = 86400
    max_sessions: int = 256
    max_queued_commands: int = 64
    max_output_chars: int = 20_000
    max_command_chars: int = 16_384
    max_concurrent_commands: int = 4
    max_request_bytes: int = 1_048_576
    test_mode: bool = False

    def __post_init__(self) -> None:
        mode = self.auth_mode.strip().lower() if isinstance(self.auth_mode, str) else ""
        if mode not in {"bearer", "none"}:
            raise ConfigurationError("REMOTE_CODING_AGENT_AUTH_MODE must be bearer or none")
        token = self.auth_token.strip() if isinstance(self.auth_token, str) else ""
        if mode == "bearer" and (
            len(token) < 32 or token == "replace-with-a-long-random-secret"
        ):
            raise ConfigurationError(
                "MCP_AUTH_TOKEN must contain at least 32 characters of secret material "
                "when REMOTE_CODING_AGENT_AUTH_MODE=bearer"
            )
        if not isinstance(self.test_mode, bool):
            raise ConfigurationError("REMOTE_CODING_AGENT_TEST_MODE must be a boolean")
        if not isinstance(self.host, str) or not self.host.strip():
            raise ConfigurationError("REMOTE_CODING_AGENT_HOST must not be empty")
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise ConfigurationError("REMOTE_CODING_AGENT_PORT must be an integer")
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("REMOTE_CODING_AGENT_PORT must be between 1 and 65535")

        fields = (
            "default_timeout_seconds", "max_timeout_seconds",
            "command_queue_timeout_seconds", "session_idle_timeout_seconds",
            "max_sessions", "max_queued_commands", "max_output_chars",
            "max_command_chars", "max_concurrent_commands", "max_request_bytes",
        )
        for name in fields:
            value = getattr(self, name)
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
        if not 1 <= self.command_queue_timeout_seconds <= 300:
            raise ConfigurationError("command_queue_timeout_seconds must be between 1 and 300")
        if not 60 <= self.session_idle_timeout_seconds <= 604800:
            raise ConfigurationError("session_idle_timeout_seconds must be between 60 and 604800")
        if not 1 <= self.max_sessions <= 10_000:
            raise ConfigurationError("max_sessions must be between 1 and 10000")
        if not 1 <= self.max_queued_commands <= 1024:
            raise ConfigurationError("max_queued_commands must be between 1 and 1024")
        if not 1 <= self.max_output_chars <= 1_000_000:
            raise ConfigurationError("max_output_chars must be between 1 and 1000000")
        if not 1 <= self.max_command_chars <= 65_536:
            raise ConfigurationError("max_command_chars must be between 1 and 65536")
        if not 1 <= self.max_concurrent_commands <= 16:
            raise ConfigurationError("max_concurrent_commands must be between 1 and 16")
        if not 1 <= self.max_request_bytes <= 10_485_760:
            raise ConfigurationError("max_request_bytes must be between 1 and 10485760")
        object.__setattr__(self, "auth_token", token)
        object.__setattr__(self, "auth_mode", mode)
        object.__setattr__(self, "workspace", Path(self.workspace).expanduser())

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from environment variables, failing closed without a token."""
        auth_mode = os.getenv("REMOTE_CODING_AGENT_AUTH_MODE", "bearer").strip().lower()
        if auth_mode not in {"bearer", "none"}:
            raise ConfigurationError("REMOTE_CODING_AGENT_AUTH_MODE must be bearer or none")
        token = os.getenv("MCP_AUTH_TOKEN", "").strip()
        if auth_mode == "bearer" and not token:
            raise ConfigurationError("MCP_AUTH_TOKEN must be set when auth mode is bearer")
        raw_test_mode = os.getenv("REMOTE_CODING_AGENT_TEST_MODE", "false").strip().lower()
        if raw_test_mode not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            raise ConfigurationError("REMOTE_CODING_AGENT_TEST_MODE must be true or false")
        test_mode = raw_test_mode in {"1", "true", "yes", "on"}

        def setting(name: str, default: int) -> int:
            return _positive_int(name, os.getenv(name), default)

        return cls(
            auth_token=token,
            auth_mode=auth_mode,
            host=os.getenv("REMOTE_CODING_AGENT_HOST", "127.0.0.1").strip(),
            port=setting("REMOTE_CODING_AGENT_PORT", 8000),
            workspace=Path(os.getenv("REMOTE_CODING_AGENT_WORKSPACE", "./workspace")).expanduser(),
            default_timeout_seconds=setting("REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS", 120),
            max_timeout_seconds=setting("REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS", 3600),
            command_queue_timeout_seconds=setting(
                "REMOTE_CODING_AGENT_COMMAND_QUEUE_TIMEOUT_SECONDS", 30
            ),
            session_idle_timeout_seconds=setting(
                "REMOTE_CODING_AGENT_SESSION_IDLE_TIMEOUT_SECONDS", 86400
            ),
            max_sessions=setting("REMOTE_CODING_AGENT_MAX_SESSIONS", 256),
            max_queued_commands=setting("REMOTE_CODING_AGENT_MAX_QUEUED_COMMANDS", 64),
            max_output_chars=setting("REMOTE_CODING_AGENT_MAX_OUTPUT_CHARS", 20_000),
            max_command_chars=setting("REMOTE_CODING_AGENT_MAX_COMMAND_CHARS", 16_384),
            max_concurrent_commands=setting("REMOTE_CODING_AGENT_MAX_CONCURRENT_COMMANDS", 4),
            max_request_bytes=setting("REMOTE_CODING_AGENT_MAX_REQUEST_BYTES", 1_048_576),
            test_mode=test_mode,
        )
