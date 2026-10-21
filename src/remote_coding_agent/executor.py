"""Bounded subprocess execution inside the configured workspace."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any


class CommandExecutionError(ValueError):
    """Raised when a command request is invalid or outside the allowed workspace."""


def _resolve_working_directory(workspace: Path, requested: str | None) -> Path:
    root = workspace.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = root if requested is None or not requested.strip() else (root / requested).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CommandExecutionError("working_directory must stay inside the configured workspace") from exc
    if not candidate.exists():
        raise CommandExecutionError("working_directory does not exist")
    if not candidate.is_dir():
        raise CommandExecutionError("working_directory must be a directory")
    return candidate


def _limit_output(stdout: str, stderr: str, limit: int) -> tuple[str, str, bool]:
    if limit < 1:
        raise ValueError("max_output_chars must be greater than zero")
    total = len(stdout) + len(stderr)
    if total <= limit:
        return stdout, stderr, False

    marker = "\n...[output truncated]"
    usable = max(0, limit - len(marker))
    kept_stdout = stdout[:usable]
    remaining = usable - len(kept_stdout)
    kept_stderr = stderr[:remaining]
    if len(kept_stdout) + len(kept_stderr) < usable:
        kept_stderr += stderr[len(kept_stderr) : max(len(kept_stderr), remaining)]
    if len(kept_stdout) + len(kept_stderr) < usable and not stdout:
        kept_stderr = stderr[:usable]
    if kept_stdout:
        kept_stdout += marker
    else:
        kept_stderr += marker
    return kept_stdout, kept_stderr, True


async def execute_command(
    *,
    command: str,
    workspace: Path,
    timeout_seconds: int,
    max_timeout_seconds: int,
    max_output_chars: int,
    working_directory: str | None = None,
) -> dict[str, Any]:
    """Run a shell command and return bounded output plus execution metadata."""
    if not command or not command.strip():
        raise CommandExecutionError("command must not be empty")
    if timeout_seconds < 1:
        raise CommandExecutionError("timeout_seconds must be greater than zero")
    if timeout_seconds > max_timeout_seconds:
        raise CommandExecutionError(
            f"timeout_seconds cannot exceed the configured maximum of {max_timeout_seconds}"
        )

    cwd = _resolve_working_directory(workspace, working_directory)
    started = time.monotonic()
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        timed_out = True
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()

    duration = round(time.monotonic() - started, 3)
    stdout, stderr, output_truncated = _limit_output(
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        max_output_chars,
    )
    return {
        "command": command,
        "working_directory": str(cwd),
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "output_truncated": output_truncated,
        "duration_seconds": duration,
    }
