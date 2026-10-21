"""Tests for bounded shell execution and workspace restrictions."""

import asyncio
import os
import time
from pathlib import Path

import pytest

from remote_coding_agent.executor import CommandExecutionError, execute_command


def run(command: str, workspace: Path, **kwargs):
    return asyncio.run(
        execute_command(
            command=command,
            workspace=workspace,
            timeout_seconds=kwargs.pop("timeout_seconds", 2),
            max_timeout_seconds=kwargs.pop("max_timeout_seconds", 5),
            max_output_chars=kwargs.pop("max_output_chars", 1000),
            max_command_chars=kwargs.pop("max_command_chars", 16_384),
            **kwargs,
        )
    )


def test_executes_command_and_returns_exit_code(tmp_path: Path) -> None:
    result = run("printf 'hello'", tmp_path)
    assert result["stdout"] == "hello"
    assert result["exit_code"] == 0
    assert result["timed_out"] is False


def test_preserves_nonzero_exit_code(tmp_path: Path) -> None:
    result = run("exit 7", tmp_path)
    assert result["exit_code"] == 7


def test_rejects_empty_command(tmp_path: Path) -> None:
    with pytest.raises(CommandExecutionError, match="must not be empty"):
        run("  ", tmp_path)


def test_rejects_working_directory_outside_workspace(tmp_path: Path) -> None:
    with pytest.raises(CommandExecutionError, match="inside"):
        run("pwd", tmp_path, working_directory="../")


def test_rejects_timeout_above_configured_maximum(tmp_path: Path) -> None:
    with pytest.raises(CommandExecutionError, match="maximum"):
        run("sleep 1", tmp_path, timeout_seconds=6, max_timeout_seconds=5)


def test_times_out_long_running_command(tmp_path: Path) -> None:
    result = run("sleep 2", tmp_path, timeout_seconds=1)
    assert result["timed_out"] is True
    assert result["exit_code"] != 0


def test_timeout_kills_descendant_that_keeps_pipes_open(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("process-group cleanup is POSIX-specific")
    started = time.monotonic()
    result = run("(sleep 10) & wait", tmp_path, timeout_seconds=1)
    elapsed = time.monotonic() - started
    assert result["timed_out"] is True
    assert elapsed < 4


def test_caps_large_stdout_and_stderr_without_unbounded_capture(tmp_path: Path) -> None:
    result = run(
        "yes x | head -c 2000000; yes y | head -c 2000000 >&2",
        tmp_path,
        timeout_seconds=3,
        max_output_chars=128,
    )
    assert result["output_truncated"] is True
    assert len(result["stdout"]) + len(result["stderr"]) <= 128


def test_truncates_oversized_output(tmp_path: Path) -> None:
    result = run("printf 'abcdefghijklmnop'", tmp_path, max_output_chars=10)
    assert result["output_truncated"] is True
    assert len(result["stdout"]) + len(result["stderr"]) <= 10


def test_rejects_invalid_output_limit(tmp_path: Path) -> None:
    with pytest.raises(CommandExecutionError, match="max_output_chars"):
        run("printf ok", tmp_path, max_output_chars=0)


def test_symlink_working_directory_cannot_escape_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(CommandExecutionError, match="inside"):
        run("pwd", tmp_path, working_directory="escape")


def test_child_process_does_not_receive_secret_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "server-secret-token")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret-token")
    monkeypatch.setenv("PUBLIC_TEST_VALUE", "visible")
    result = run(
        'printf "%s|%s|%s" "$MCP_AUTH_TOKEN" "$GITHUB_TOKEN" "$PUBLIC_TEST_VALUE"',
        tmp_path,
    )
    assert result["stdout"] == "||visible"
