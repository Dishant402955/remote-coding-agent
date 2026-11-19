"""Run the Remote Coding Agent MCP server."""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from remote_coding_agent.auth import BearerTokenMiddleware
from remote_coding_agent.config import Settings
from remote_coding_agent.executor import CommandExecutionError, execute_command

logger = logging.getLogger("remote_coding_agent")
_HISTORY_LOCK = threading.Lock()
_STATE_DIR = Path(os.getenv(
    "REMOTE_CODING_AGENT_STATE_DIR",
    str(Path.home() / ".local" / "state" / "remote-coding-agent"),
)).expanduser()
_HISTORY_FILE = _STATE_DIR / "command-history.jsonl"


def _record_command(event: dict[str, Any]) -> None:
    """Append a redacted command lifecycle event to the private dashboard history."""
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(_STATE_DIR, 0o700)
        payload = json.dumps(event, separators=(",", ":")) + "\n"
        with _HISTORY_LOCK:
            descriptor = os.open(_HISTORY_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(descriptor, payload.encode("utf-8"))
            finally:
                os.close(descriptor)
            os.chmod(_HISTORY_FILE, 0o600)
            if _HISTORY_FILE.stat().st_size > 1_048_576:
                recent = _HISTORY_FILE.read_text(encoding="utf-8").splitlines()[-1000:]
                temporary = _HISTORY_FILE.with_suffix(".tmp")
                with temporary.open("w", encoding="utf-8") as handle:
                    handle.write("\n".join(recent) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, _HISTORY_FILE)
    except OSError:
        logger.warning("Could not persist dashboard command history")

_REDACTION_PATTERNS = (
    re.compile(
        r"(?i)([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIAL|AUTHORIZATION)[A-Z0-9_]*\s*=\s*)(?:\"[^\"]*\"|'[^']*'|[^\s;&|]+)"
    ),
    re.compile(
        r"(?i)(--?(?:token|secret|password|passwd|api[-_]?key|access[-_]?key|private[-_]?key|credential|authorization)(?:=|\s+))(?:\"[^\"]*\"|'[^']*'|[^\s;&|]+)"
    ),
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)([?&](?:access_token|api_key|token|secret|password)=)[^&\s]+"),
)


def _redact_sensitive_text(value: str) -> str:
    """Redact common inline credentials before command/output history is persisted."""
    redacted = value
    for pattern in _REDACTION_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted


def _command_preview(command: str, limit: int = 240) -> str:
    """Return a short command preview with common credential forms redacted."""
    preview = command
    for pattern in _REDACTION_PATTERNS:
        preview = pattern.sub(r"\1[REDACTED]", preview)
    preview = " ".join(preview.split())
    return preview if len(preview) <= limit else preview[: limit - 3] + "..."


class MCPAccessLogMiddleware:
    """Log MCP HTTP access without recording headers, bodies, or session IDs."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/mcp":
            await self.app(scope, receive, send)
            return

        access_id = uuid.uuid4().hex[:8]
        method = str(scope.get("method", "UNKNOWN"))
        started = time.monotonic()
        status_code = 500
        completed = False
        logger.info("ACCESS START id=%s method=%s path=/mcp", access_id, method)

        async def logged_send(message: dict[str, Any]) -> None:
            nonlocal status_code, completed
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)
            if (
                message.get("type") == "http.response.body"
                and not message.get("more_body", False)
                and not completed
            ):
                completed = True
                logger.info(
                    "ACCESS END id=%s method=%s path=/mcp status=%s duration_ms=%.1f",
                    access_id,
                    method,
                    status_code,
                    (time.monotonic() - started) * 1000,
                )

        try:
            await self.app(scope, receive, logged_send)
        except asyncio.CancelledError:
            logger.info("ACCESS CANCELLED id=%s method=%s path=/mcp", access_id, method)
            raise
        except BaseException:
            logger.exception("ACCESS FAILED id=%s method=%s path=/mcp", access_id, method)
            raise
        finally:
            if not completed:
                logger.info(
                    "ACCESS INCOMPLETE id=%s method=%s path=/mcp status=%s duration_ms=%.1f",
                    access_id,
                    method,
                    status_code,
                    (time.monotonic() - started) * 1000,
                )


_TEST_MODE_COMMANDS = frozenset(
    {
        "pwd",
        "ls -la",
        "python --version",
        "printf 'mcp-test-ok'",
    }
)


def _disable_linux_process_inspection() -> None:
    """Prevent same-UID processes from inspecting this server's memory on Linux."""
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, "could not disable Linux process inspection")


def _configure_logging() -> None:
    """Send concise application logs to stdout; the supervisor rotates this stream."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    for name in ("uvicorn.access", "mcp", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True


def _busy_result(message: str) -> dict[str, Any]:
    return {
        "stdout": "",
        "stderr": message,
        "exit_code": 75,
        "timed_out": False,
        "output_truncated": False,
        "duration_seconds": 0.0,
    }


def create_server(settings: Settings) -> tuple[FastMCP, Any]:
    """Build the MCP server with bounded HTTP requests, sessions, and command admission."""
    settings.workspace.resolve().mkdir(parents=True, exist_ok=True)
    mcp = FastMCP(
        "Remote Coding Agent",
        instructions=(
            "Execute development commands in the configured remote workspace. "
            "Commands can modify files and the environment. Inspect results and diffs "
            "carefully; never assume a command succeeded without checking its exit code."
        ),
        host=settings.host,
        port=settings.port,
        max_request_body_size=settings.max_request_bytes,
        session_idle_timeout=float(settings.session_idle_timeout_seconds),
        max_sessions=settings.max_sessions,
    )
    execution_slots = asyncio.Semaphore(settings.max_concurrent_commands)
    queue_lock = asyncio.Lock()
    queued_commands = 0

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> JSONResponse:
        """Return liveness for the MCP server process."""
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/readyz", methods=["GET"], include_in_schema=False)
    async def readyz(_: Request) -> JSONResponse:
        """Return readiness once the ASGI application has started."""
        return JSONResponse({"status": "ready", "service": "remote-coding-agent"})

    @mcp.tool()
    async def run_command(
        command: str,
        working_directory: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Run one shell command with bounded concurrency and queueing.

        Args:
            command: Shell command to execute in the remote workspace.
            working_directory: Optional directory relative to the workspace root.
            timeout_seconds: Optional timeout, capped by the server configuration.
        """
        nonlocal queued_commands
        invocation_id = uuid.uuid4().hex[:8]
        started = time.monotonic()
        timeout = settings.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        preview = (
            _command_preview(command)
            if isinstance(command, str)
            else f"<invalid:{type(command).__name__}>"
        )
        requested_cwd = (
            _command_preview(working_directory, 120)
            if isinstance(working_directory, str)
            else "<workspace-root>"
        )
        started_at = datetime.now(UTC).isoformat(timespec="milliseconds")
        _record_command({
            "id": invocation_id,
            "started_at": started_at,
            "command": _redact_sensitive_text(command) if isinstance(command, str) else preview,
            "cwd": requested_cwd,
            "timeout_seconds": timeout,
            "status": "running",
        })
        logger.info(
            "TOOL START id=%s name=run_command command=%s cwd=%s timeout_seconds=%s",
            invocation_id,
            preview,
            requested_cwd,
            timeout,
        )

        def report(result: dict[str, Any]) -> dict[str, Any]:
            duration = time.monotonic() - started
            _record_command({
                "id": invocation_id,
                "finished_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "duration_seconds": round(duration, 3),
                "exit_code": result.get("exit_code"),
                "status": (
                    "success"
                    if result.get("exit_code") == 0 and not result.get("timed_out")
                    else "failed"
                ),
                "timed_out": bool(result.get("timed_out")),
                "stdout": _redact_sensitive_text(str(result.get("stdout", ""))),\n                "stderr": _redact_sensitive_text(str(result.get("stderr", ""))),\n                "output_truncated": bool(result.get("output_truncated")),\n                "command": _redact_sensitive_text(command) if isinstance(command, str) else preview,\n                "cwd": requested_cwd,\n                "timeout_seconds": timeout,
            })
            logger.info(
                "TOOL END id=%s name=run_command exit_code=%s timed_out=%s "
                "duration_seconds=%.3f output_truncated=%s",
                invocation_id,
                result.get("exit_code"),
                result.get("timed_out"),
                duration,
                result.get("output_truncated"),
            )
            return result

        if not isinstance(command, str) or len(command) > settings.max_command_chars:
            return report(
                {
                    "stdout": "",
                    "stderr": (
                        "command must be a string no longer than "
                        f"{settings.max_command_chars} characters"
                    ),
                    "exit_code": 2,
                    "timed_out": False,
                    "output_truncated": False,
                    "duration_seconds": 0.0,
                }
            )
        if settings.test_mode and command.strip() not in _TEST_MODE_COMMANDS:
            return report(
                {
                    "stdout": "",
                    "stderr": "test mode only permits these exact commands: "
                    + ", ".join(sorted(_TEST_MODE_COMMANDS)),
                    "exit_code": 126,
                    "timed_out": False,
                    "output_truncated": False,
                    "duration_seconds": 0.0,
                }
            )

        queued = False
        acquired = False
        try:
            async with queue_lock:
                if execution_slots.locked():
                    if queued_commands >= settings.max_queued_commands:
                        logger.warning(
                            "TOOL REJECTED id=%s reason=command_queue_full", invocation_id
                        )
                        return report(
                            _busy_result("server is busy; command queue is full, retry later")
                        )
                    queued_commands += 1
                    queued = True
            if queued:
                try:
                    await asyncio.wait_for(
                        execution_slots.acquire(),
                        timeout=settings.command_queue_timeout_seconds,
                    )
                except TimeoutError:
                    return report(
                        _busy_result(
                            "server is busy; command waited too long for an execution slot"
                        )
                    )
            else:
                await execution_slots.acquire()
            acquired = True
            result = await execute_command(
                command=command,
                workspace=Path(settings.workspace),
                working_directory=working_directory,
                timeout_seconds=timeout,
                max_timeout_seconds=settings.max_timeout_seconds,
                max_output_chars=settings.max_output_chars,
                max_command_chars=settings.max_command_chars,
            )
            return report(result)
        except CommandExecutionError as exc:
            logger.info("TOOL REJECTED id=%s reason=%s", invocation_id, type(exc).__name__)
            return report(
                {
                    "stdout": "",
                    "stderr": str(exc),
                    "exit_code": 2,
                    "timed_out": False,
                    "output_truncated": False,
                    "duration_seconds": 0.0,
                }
            )
        except OSError as exc:
            logger.warning("TOOL FAILED id=%s error=%s", invocation_id, type(exc).__name__)
            return report(
                {
                    "stdout": "",
                    "stderr": f"command execution failed: {type(exc).__name__}",
                    "exit_code": 126,
                    "timed_out": False,
                    "output_truncated": False,
                    "duration_seconds": 0.0,
                }
            )
        finally:
            if queued:
                async with queue_lock:
                    queued_commands -= 1
            if acquired:
                execution_slots.release()

    expected_token = settings.auth_token if settings.auth_mode == "bearer" else None
    app = BearerTokenMiddleware(
        mcp.streamable_http_app(), expected_token, settings.max_request_bytes
    )
    return mcp, MCPAccessLogMiddleware(app)


def main() -> None:
    """Load configuration and serve the MCP endpoint over Streamable HTTP."""
    _configure_logging()
    settings = Settings.from_env()
    _disable_linux_process_inspection()
    _, app = create_server(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        timeout_keep_alive=30,
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
