"""Run the authenticated Remote Coding Agent MCP server."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP

from remote_coding_agent.auth import BearerTokenMiddleware
from remote_coding_agent.config import Settings
from remote_coding_agent.executor import CommandExecutionError, execute_command

logger = logging.getLogger("remote_coding_agent")


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
        try:
            return await execute_command(
                command=command,
                workspace=Path(settings.workspace),
                working_directory=working_directory,
                timeout_seconds=timeout,
                max_timeout_seconds=settings.max_timeout_seconds,
                max_output_chars=settings.max_output_chars,
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


    app = BearerTokenMiddleware(mcp.streamable_http_app(), settings.auth_token)
    return mcp, app


def main() -> None:
    """Load configuration and serve the MCP endpoint over Streamable HTTP."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    _, app = create_server(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
