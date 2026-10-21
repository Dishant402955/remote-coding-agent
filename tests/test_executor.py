"""Tests for bounded shell execution and workspace restrictions."""

import asyncio
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


def test_truncates_oversized_output(tmp_path: Path) -> None:
    result = run("printf 'abcdefghijklmnop'", tmp_path, max_output_chars=10)
    assert result["output_truncated"] is True
    assert len(result["stdout"]) <= 10
