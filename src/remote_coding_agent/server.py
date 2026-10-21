"""Run the authenticated Remote Coding Agent MCP server."""

from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP

from remote_coding_agent.auth import BearerTokenMiddleware
from remote_coding_agent.config import Settings
from remote_coding_agent.executor import CommandExecutionError, execute_command

logger = logging.getLogger("remote_coding_agent")


def _disable_linux_process_inspection() -> None:
    """Prevent same-UID processes from reading this server's memory/environment on Linux."""
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    # Linux PR_SET_DUMPABLE=4; zero disables ptrace-based inspection and core dumps.
    if libc.prctl(4, 0, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, "could not disable Linux process inspection")


def create_server(settings: Settings) -> tuple[FastMCP, Any]:
    """Build the MCP server and wrap its HTTP app with mandatory bearer auth."""
    settings.workspace.expanduser().resolve().mkdir(parents=True, exist_ok=True)
    mcp = FastMCP(
        "Remote Coding Agent",
        instructions=(
            "Execute development commands in the configured remote workspace. "
            "Commands can modify files and the environment. Inspect results and diffs "
            "carefully; never assume a command succeeded without checking its exit code."
        ),
        host=settings.host,
        port=settings.port,
    )
    execution_slots = asyncio.Semaphore(settings.max_concurrent_commands)

    @mcp.tool()
    async def run_command(
        command: str,
        working_directory: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Run one shell command in the configured workspace.

        Args:
            command: Shell command to execute in the remote workspace.
            working_directory: Optional directory relative to the workspace root.
            timeout_seconds: Optional timeout, capped by the server configuration.
        """
        timeout = (
            settings.default_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if not isinstance(command, str) or len(command) > settings.max_command_chars:
            return {
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

        if execution_slots.locked():
            return {
                "stdout": "",
                "stderr": "server is busy; retry after a running command finishes",
                "exit_code": 75,
                "timed_out": False,
                "output_truncated": False,
                "duration_seconds": 0.0,
            }

        await execution_slots.acquire()
        try:
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
                "command": command,
                "stdout": "",
                "stderr": str(exc),
                "exit_code": 2,
                "timed_out": False,
                "output_truncated": False,
                "duration_seconds": 0.0,
            }
        finally:
            execution_slots.release()

    app = BearerTokenMiddleware(
        mcp.streamable_http_app(), settings.auth_token, settings.max_request_bytes
    )
    return mcp, app


def main() -> None:
    """Load configuration and serve the MCP endpoint over Streamable HTTP."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    _disable_linux_process_inspection()
    _, app = create_server(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
