"""Tests for the process supervisors used by long-running deployments."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "server-supervisor.sh"
TUNNEL = ROOT / "scripts" / "tunnel-client-supervisor.sh"


def wait_for(predicate, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition was not met before timeout")


@pytest.mark.skipif(os.name != "posix", reason="process-group supervision is POSIX-specific")
def test_server_supervisor_restarts_crashed_child(tmp_path: Path) -> None:
    count = tmp_path / "starts"
    child = tmp_path / "child.py"
    child.write_text(
        "import pathlib,time\n"
        f"p=pathlib.Path({str(count)!r})\n"
        "p.write_text(str(int(p.read_text())+1 if p.exists() else 1))\n"
        "time.sleep(.2)\nraise SystemExit(17)\n",
        encoding="utf-8",
    )
    log = tmp_path / "server.log"
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("REMOTE_CODING_AGENT_")
    }
    clean_env["REMOTE_CODING_AGENT_CHILD_LOG_FILE"] = str(tmp_path / "server-child.log")
    process = subprocess.Popen(
        ["bash", str(SERVER), str(log), sys.executable, str(child)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=clean_env,
        start_new_session=True,
    )
    try:
        wait_for(lambda: count.exists() and int(count.read_text()) >= 2)
        assert "STATUS: RESTARTING" in log.read_text()
    finally:
        process.terminate()
        process.wait(timeout=8)
    log_text = log.read_text()
    assert "STATUS: STOPPING" in log_text
    assert "STATUS: STOPPED" in log_text


@pytest.mark.skipif(
    os.name != "posix" or not Path("/proc/self/fd").exists(),
    reason="Linux process descriptors and process groups are required",
)
def test_server_supervisor_closes_inherited_lock_fd_and_reaps_process_group(
    tmp_path: Path,
) -> None:
    lock_file = tmp_path / "up.lock"
    child_pid_file = tmp_path / "grandchild.pid"
    fd_marker = tmp_path / "lock-fd-inherited"
    child = tmp_path / "child.py"
    child.write_text(
        "import os,pathlib,subprocess,time\n"
        f"pathlib.Path({str(fd_marker)!r}).write_text(str(os.path.exists('/proc/self/fd/9')))\n"
        f"p=pathlib.Path({str(child_pid_file)!r})\n"
        "p.write_text(str(subprocess.Popen(['sleep','60']).pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    log = tmp_path / "server.log"
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("REMOTE_CODING_AGENT_")
    }
    clean_env["REMOTE_CODING_AGENT_CHILD_LOG_FILE"] = str(tmp_path / "server-child.log")
    process = subprocess.Popen(
        [
            "bash",
            "-c",
            'exec 9>"$1"; shift; exec "$@"',
            "_",
            str(lock_file),
            "bash",
            str(SERVER),
            str(log),
            sys.executable,
            str(child),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=clean_env,
        start_new_session=True,
    )
    try:
        wait_for(lambda: fd_marker.exists() and child_pid_file.exists())
        assert fd_marker.read_text() == "False"
        grandchild_pid = int(child_pid_file.read_text())
        process.terminate()
        process.wait(timeout=8)

        def child_is_gone() -> bool:
            try:
                os.kill(grandchild_pid, 0)
            except ProcessLookupError:
                return True
            status_path = Path(f"/proc/{grandchild_pid}/stat")
            if status_path.exists():
                return status_path.read_text().split()[2] == "Z"
            return False

        wait_for(child_is_gone, timeout=4.0)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=8)
    status_log = log.read_text()
    assert "STATUS: STOPPING" in status_log
    assert "STATUS: STOPPED" in status_log


@pytest.mark.skipif(os.name != "posix", reason="process-group supervision is POSIX-specific")
def test_tunnel_supervisor_restarts_with_24h_ttl_and_hides_key(tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    key_seen = tmp_path / "key-seen"
    args_seen = tmp_path / "args-seen"
    fake = tmp_path / "tunnel-client"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "doctor" ]]; then exit 0; fi\n'
        f"printf '%s\\n' \"$*\" >> {str(calls)!r}\n"
        f"printf '%s' \"$*\" > {str(args_seen)!r}\n"
        f"printf '%s' \"$CONTROL_PLANE_API_KEY\" > {str(key_seen)!r}\n"
        "exit 17\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    log = tmp_path / "supervisor.log"
    client_log = tmp_path / "client.log"
    secret = "test-runtime-key-never-write-to-logs-or-arguments"
    with client_log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            ["bash", str(TUNNEL), str(fake), "http://127.0.0.1:8000/mcp", str(log), "24h", ""],
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.STDOUT,
            env={
                **{
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith("REMOTE_CODING_AGENT_")
                },
                "CONTROL_PLANE_TUNNEL_ID": "tunnel_test",
            },
            start_new_session=True,
        )
        assert process.stdin is not None
        process.stdin.write((secret + "\n").encode())
        process.stdin.close()
        try:
            wait_for(lambda: calls.exists() and len(calls.read_text().splitlines()) >= 2)
            assert key_seen.read_text() == secret
        finally:
            process.terminate()
            process.wait(timeout=8)
    assert "--mcp.connection-max-ttl=24h" in calls.read_text()
    assert secret not in log.read_text()
    assert secret not in client_log.read_text()
    assert secret not in args_seen.read_text()
    status_log = log.read_text()
    assert "STATUS: STARTING" in status_log
    assert "STATUS: RESTARTING" in status_log
    assert "STATUS: STOPPING" in status_log
    assert "STATUS: STOPPED" in status_log


@pytest.mark.skipif(os.name != "posix", reason="process-group supervision is POSIX-specific")
def test_tunnel_supervisor_restarts_after_control_plane_health_fails(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls"
    fake_client = tmp_path / "tunnel-client"
    fake_client.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "doctor" ]]; then exit 0; fi\n'
        f"printf 'started\\n' >> {str(calls)!r}\n"
        "sleep 30 &\n"
        "wait $!\n",
        encoding="utf-8",
    )
    fake_client.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do url="$arg"; done\n'
        'case "$url" in\n'
        "  */healthz) printf live; exit 0 ;;\n"
        "  */readyz) printf ready; exit 0 ;;\n"
        '  */health/control-plane) printf \'{"status":"down"}\'; exit 0 ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    log = tmp_path / "tunnel-supervisor.log"
    client_log = tmp_path / "client.log"
    env = {
        **{
            key: value
            for key, value in os.environ.items()
            if not key.startswith("REMOTE_CODING_AGENT_")
        },
        "PATH": f"{fake_bin}:/usr/local/bin:/usr/bin:/bin",
        "CONTROL_PLANE_TUNNEL_ID": "tunnel_test",
        "REMOTE_CODING_AGENT_TUNNEL_HEALTH_INTERVAL_SECONDS": "0.1",
        "REMOTE_CODING_AGENT_TUNNEL_STARTUP_GRACE_SECONDS": "0",
        "REMOTE_CODING_AGENT_TUNNEL_HEALTH_FAILURE_LIMIT": "2",
        "REMOTE_CODING_AGENT_TUNNEL_CLIENT_LOG": str(client_log),
    }
    with client_log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [
                "bash",
                str(TUNNEL),
                str(fake_client),
                "http://127.0.0.1:8000/mcp",
                str(log),
                "24h",
                "http://127.0.0.1:18080",
            ],
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        assert process.stdin is not None
        process.stdin.write(b"test-key\n")
        process.stdin.close()
        try:
            wait_for(
                lambda: calls.exists() and len(calls.read_text().splitlines()) >= 2, timeout=8.0
            )
            status_log = log.read_text()
            assert "STATUS: DEGRADED" in status_log
            assert "STATUS: RESTARTING" in status_log
        finally:
            process.terminate()
            process.wait(timeout=8)


@pytest.mark.skipif(os.name != "posix", reason="process-group supervision is POSIX-specific")
def test_tunnel_supervisor_terminates_child_process_group_on_shutdown(
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "grandchild.pid"
    fake_client = tmp_path / "tunnel-client"
    fake_client.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "doctor" ]]; then exit 0; fi\n'
        "sleep 60 &\n"
        f"printf '%s' \"$!\" > {str(child_pid_file)!r}\n"
        "wait $!\n",
        encoding="utf-8",
    )
    fake_client.chmod(0o755)
    log = tmp_path / "supervisor.log"
    client_log = tmp_path / "client.log"
    env = {
        **{
            key: value
            for key, value in os.environ.items()
            if not key.startswith("REMOTE_CODING_AGENT_")
        },
        "CONTROL_PLANE_TUNNEL_ID": "tunnel_test",
        "REMOTE_CODING_AGENT_TUNNEL_CLIENT_LOG": str(client_log),
    }
    with client_log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [
                "bash",
                str(TUNNEL),
                str(fake_client),
                "http://127.0.0.1:8000/mcp",
                str(log),
                "24h",
                "",
            ],
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        assert process.stdin is not None
        process.stdin.write(b"test-key\n")
        process.stdin.close()
        try:
            wait_for(child_pid_file.exists)
            grandchild_pid = int(child_pid_file.read_text())
            process.terminate()
            process.wait(timeout=8)

            def child_is_gone() -> bool:
                try:
                    os.kill(grandchild_pid, 0)
                except ProcessLookupError:
                    return True
                status_path = Path(f"/proc/{grandchild_pid}/stat")
                if status_path.exists():
                    return status_path.read_text().split()[2] == "Z"
                return False

            wait_for(child_is_gone, timeout=4.0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)


@pytest.mark.skipif(os.name != "posix", reason="process supervision is POSIX-specific")
def test_server_supervisor_stops_promptly_during_restart_backoff(
    tmp_path: Path,
) -> None:
    child = tmp_path / "exit.py"
    child.write_text("raise SystemExit(9)\n", encoding="utf-8")
    log = tmp_path / "supervisor.log"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("REMOTE_CODING_AGENT_")
    }
    env["REMOTE_CODING_AGENT_CHILD_LOG_FILE"] = str(tmp_path / "child.log")
    process = subprocess.Popen(
        ["bash", str(SERVER), str(log), sys.executable, str(child)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    try:
        wait_for(lambda: log.exists() and "STATUS: RESTARTING" in log.read_text())
        started = time.monotonic()
        process.terminate()
        process.wait(timeout=3)
        assert time.monotonic() - started < 3
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
    assert "STATUS: STOPPING" in log.read_text()
    assert "STATUS: STOPPED" in log.read_text()


@pytest.mark.skipif(os.name != "posix", reason="process-group supervision is POSIX-specific")
def test_server_supervisor_tees_child_output_to_terminal_and_log(tmp_path: Path) -> None:
    child_log = tmp_path / "server-child.log"
    terminal_log = tmp_path / "terminal.log"
    status_log = tmp_path / "server-status.log"
    child = tmp_path / "child.py"
    child.write_text(
        "import time\nprint('visible-agent-output', flush=True)\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("REMOTE_CODING_AGENT_")
    }
    clean_env["REMOTE_CODING_AGENT_CHILD_LOG_FILE"] = str(child_log)
    with terminal_log.open("w", encoding="utf-8") as terminal:
        process = subprocess.Popen(
            ["bash", str(SERVER), str(status_log), sys.executable, str(child)],
            stdin=subprocess.DEVNULL,
            stdout=terminal,
            stderr=subprocess.STDOUT,
            env=clean_env,
            start_new_session=True,
        )
        try:
            wait_for(
                lambda: (
                    terminal_log.exists()
                    and "visible-agent-output" in terminal_log.read_text()
                    and child_log.exists()
                    and "visible-agent-output" in child_log.read_text()
                )
            )
        finally:
            process.terminate()
            process.wait(timeout=8)
    assert "visible-agent-output" in terminal_log.read_text()
    assert "visible-agent-output" in child_log.read_text()
