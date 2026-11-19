"""Integration tests for the installed tunnel credential helper."""

import json
import os
import subprocess
from pathlib import Path


def test_write_helper_reads_json_from_stdin(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    source = repository / "scripts" / "store-tunnel-credentials.sh"
    tunnel_home = tmp_path / "tunnel-home"
    tunnel_home.mkdir(mode=0o700)
    helper = tmp_path / "store-tunnel-credentials.sh"
    script = source.read_text(encoding="utf-8").replace(
        'TUNNEL_HOME="/var/lib/remote-coding-agent-tunnel"',
        f'TUNNEL_HOME="{tunnel_home}"',
    )
    helper.write_text(script, encoding="utf-8")
    helper.chmod(0o755)

    expected = {
        "tunnel_id": "test-tunnel-id",
        "api_key": "test-runtime-api-key-123456",
    }
    result = subprocess.run(
        ["bash", str(helper), "write"],
        input=json.dumps(expected),
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    stored = tunnel_home / "credentials.json"
    assert json.loads(stored.read_text(encoding="utf-8")) == expected
    assert stored.stat().st_mode & 0o777 == 0o600
