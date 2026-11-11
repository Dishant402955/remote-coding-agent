"""Run the Remote Coding Agent MCP server."""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from remote_coding_agent.auth import BearerTokenMiddleware
from remote_coding_agent.config import Settings
from remote_coding_agent.executor import CommandExecutionError, execute_command

logger = logging.getLogger("remote_coding_agent")
_TEST_MODE_COMMANDS = frozenset({
    "pwd", "ls -la", "python --version", "printf 'mcp-test-ok'",
})


def _disable_linux_process_inspection() -> None:
    """Prevent same-UID processes from inspecting this server's memory on Linux."""
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, "could not disable Linux process inspection")


def _configure_logging() -> None:
    """Configure bounded rotating logs without logging commands or secrets."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    log_path = os.getenv("REMOTE_CODING_AGENT_LOG_FILE", "").strip()
    if log_path:
        path = Path(log_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(
            path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        ))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("uvicorn.access").disabled = True


def _busy_result(message: str) -> dict[str, Any]:
    return {
        "stdout": "", "stderr": message, "exit_code": 75,
        "timed_out": False, "output_truncated": False, "duration_seconds": 0.0,
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
        timeout = settings.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if not isinstance(command, str) or len(command) > settings.max_command_chars:
            return {
                "stdout": "",
                "stderr": (
                    "command must be a string no longer than "
                    f"{settings.max_command_chars} characters"
                ),
                "exit_code": 2, "timed_out": False, "output_truncated": False,
                "duration_seconds": 0.0,
            }
        if settings.test_mode and command.strip() not in _TEST_MODE_COMMANDS:
            return {
                "stdout": "",
                "stderr": "test mode only permits these exact commands: "
                    + ", ".join(sorted(_TEST_MODE_COMMANDS)),
                "exit_code": 126, "timed_out": False, "output_truncated": False,
                "duration_seconds": 0.0,
            }

        queued = False
        async with queue_lock:
            if execution_slots.locked():
                if queued_commands >= settings.max_queued_commands:
                    logger.warning("Command rejected because the execution queue is full")
                    return _busy_result("server is busy; command queue is full, retry later")
                queued_commands += 1
                queued = True
        acquired = False
        try:
            if queued:
                try:
                    await asyncio.wait_for(
                        execution_slots.acquire(),
                        timeout=settings.command_queue_timeout_seconds,
                    )
                except TimeoutError:
                    return _busy_result(
                        "server is busy; command waited too long for an execution slot"
                    )
            else:
                await execution_slots.acquire()
            acquired = True
            return await execute_command(
                command=command,
                workspace=Path(settings.workspace),
                working_directory=working_directory,
                timeout_seconds=timeout,
                max_timeout_seconds=settings.max_timeout_seconds,
                max_output_chars=settings.max_output_chars,
                max_command_chars=settings.max_command_chars,
            )
        except CommandExecutionError as exc:
            return {
                "stdout": "", "stderr": str(exc), "exit_code": 2,
                "timed_out": False, "output_truncated": False, "duration_seconds": 0.0,
            }
        except OSError as exc:
            logger.warning("Command execution failed (%s)", type(exc).__name__)
            return {
                "stdout": "", "stderr": f"command execution failed: {type(exc).__name__}",
                "exit_code": 126, "timed_out": False, "output_truncated": False,
                "duration_seconds": 0.0,
            }
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
    return mcp, app


def main() -> None:
    """Load configuration and serve the MCP endpoint over Streamable HTTP."""
    _configure_logging()
    settings = Settings.from_env()
    _disable_linux_process_inspection()
    _, app = create_server(settings)
    uvicorn.run(
        app, host=settings.host, port=settings.port, log_config=None,
        access_log=False, timeout_keep_alive=30, timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
