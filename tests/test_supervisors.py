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
    process = subprocess.Popen(
        ["bash", str(SERVER), str(log), sys.executable, str(child)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        wait_for(lambda: count.exists() and int(count.read_text()) >= 2)
        assert "restarting server" in log.read_text()
    finally:
        process.terminate()
        process.wait(timeout=8)


@pytest.mark.skipif(os.name != "posix", reason="process-group supervision is POSIX-specific")
def test_tunnel_supervisor_restarts_with_24h_ttl_and_hides_key(tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    key_seen = tmp_path / "key-seen"
    fake = tmp_path / "tunnel-client"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "doctor" ]]; then exit 0; fi\n'
        f"printf '%s\\n' \"$*\" >> {str(calls)!r}\n"
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
            ["bash", str(TUNNEL), str(fake), "http://127.0.0.1:8000/mcp",
             str(log), "24h", ""],
            stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
            env={**os.environ, "CONTROL_PLANE_TUNNEL_ID": "tunnel_test"},
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
