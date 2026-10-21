"""Bounded subprocess execution inside the configured workspace."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import signal
import time
from typing import Any


_SECRET_ENV_NAME = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_KEY|PRIVATE_KEY|ACCESS_KEY|AUTHORIZATION|AUTH|COOKIE|BEARER)",
    re.IGNORECASE,
)


def _command_environment() -> dict[str, str]:
    """Avoid passing common credential-bearing environment variables to shell code."""
    excluded = {
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "SSH_AUTH_SOCK",
    }
    return {
        name: value
        for name, value in os.environ.items()
        if name not in excluded and not _SECRET_ENV_NAME.search(name)
    }


class CommandExecutionError(ValueError):
    """Raised when a command request is invalid or outside the allowed workspace."""


def _resolve_working_directory(workspace: Path, requested: str | None) -> Path:
    root = workspace.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = root if requested is None or not requested.strip() else (root / requested).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CommandExecutionError(
            "working_directory must stay inside the configured workspace"
        ) from exc
    if not candidate.exists():
        raise CommandExecutionError("working_directory does not exist")
    if not candidate.is_dir():
        raise CommandExecutionError("working_directory must be a directory")
    return candidate


def _limit_output(stdout: str, stderr: str, limit: int) -> tuple[str, str, bool]:
    if limit < 1:
        raise ValueError("max_output_chars must be greater than zero")
    if len(stdout) + len(stderr) <= limit:
        return stdout, stderr, False

    marker = "...[truncated]"
    if limit <= len(marker):
        combined = stdout + stderr
        return combined[:limit], "", True

    usable = limit - len(marker)
    if len(stdout) >= usable:
        return stdout[:usable] + marker, "", True

    kept_stdout = stdout
    kept_stderr = stderr[: usable - len(kept_stdout)]
    if len(kept_stderr) < len(stderr):
        kept_stderr += marker
    else:
        kept_stdout += marker
    return kept_stdout, kept_stderr, True


async def _drain_bounded(
    reader: asyncio.StreamReader, byte_limit: int
) -> tuple[bytes, bool]:
    """Drain a pipe fully while retaining no more than byte_limit bytes."""
    chunks: list[bytes] = []
    retained = 0
    truncated = False
    while chunk := await reader.read(64 * 1024):
        available = max(0, byte_limit - retained)
        if available:
            kept = chunk[:available]
            chunks.append(kept)
            retained += len(kept)
        if len(chunk) > available:
            truncated = True
    return b"".join(chunks), truncated


def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Kill the shell and its process group, not just the shell wrapper."""
    if process.returncode is not None:
        # A shell can exit while descendants keep its output pipes open.
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        process.kill()
    except ProcessLookupError:
        pass


async def execute_command(
    *,
    command: str,
    workspace: Path,
    timeout_seconds: int,
    max_timeout_seconds: int,
    max_output_chars: int,
    max_command_chars: int = 16_384,
    working_directory: str | None = None,
) -> dict[str, Any]:
    """Run a shell command with a bounded timeout and bounded output memory."""
    if not command or not command.strip():
        raise CommandExecutionError("command must not be empty")
    if len(command) > max_command_chars:
        raise CommandExecutionError(
            f"command cannot exceed {max_command_chars} characters"
        )
    if timeout_seconds < 1:
        raise CommandExecutionError("timeout_seconds must be greater than zero")
    if timeout_seconds > max_timeout_seconds:
        raise CommandExecutionError(
            f"timeout_seconds cannot exceed the configured maximum of {max_timeout_seconds}"
        )
    if max_output_chars < 1:
        raise CommandExecutionError("max_output_chars must be greater than zero")

    cwd = _resolve_working_directory(workspace, working_directory)
    started = time.monotonic()
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=(os.name == "posix"),
        env=_command_environment(),
    )
    assert process.stdout is not None
    assert process.stderr is not None

    # Each pipe has a strict byte budget. Readers continue draining after the
    # budget is reached, preventing a full pipe from deadlocking the child.
    stdout_task = asyncio.create_task(_drain_bounded(process.stdout, max_output_chars))
    stderr_task = asyncio.create_task(_drain_bounded(process.stderr, max_output_chars))
    completion = asyncio.gather(process.wait(), stdout_task, stderr_task)
    timed_out = False
    try:
        try:
            _, stdout_result, stderr_result = await asyncio.wait_for(
                asyncio.shield(completion), timeout=timeout_seconds
            )
        except TimeoutError:
            timed_out = True
            _kill_process_tree(process)
            _, stdout_result, stderr_result = await completion
    except asyncio.CancelledError:
        _kill_process_tree(process)
        try:
            await asyncio.shield(completion)
        finally:
            raise

    stdout_bytes, stdout_limited = stdout_result
    stderr_bytes, stderr_limited = stderr_result
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if stdout_limited:
        stdout += "...[truncated]"
    if stderr_limited:
        stderr += "...[truncated]"
    stdout, stderr, output_limited = _limit_output(stdout, stderr, max_output_chars)

    return {
        "command": command,
        "working_directory": str(cwd),
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "output_truncated": stdout_limited or stderr_limited or output_limited,
        "duration_seconds": round(time.monotonic() - started, 3),
    }
