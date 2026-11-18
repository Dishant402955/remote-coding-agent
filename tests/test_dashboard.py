"""Tests for the local dashboard's private state and password handling."""

import json

from remote_coding_agent import dashboard


def test_dashboard_password_is_salted_and_verified() -> None:
    salt, digest = dashboard._password_hash("a-strong-dashboard-password")
    config = {"password_salt": salt, "password_hash": digest}

    assert salt != dashboard._password_hash("a-strong-dashboard-password")[0]
    assert dashboard._verify_password("a-strong-dashboard-password", config)
    assert not dashboard._verify_password("wrong-password", config)


def test_dashboard_config_is_private_and_round_trips(tmp_path, monkeypatch) -> None:
    config_file = tmp_path / "dashboard.json"
    monkeypatch.setattr(dashboard, "STATE_DIR", tmp_path)
    monkeypatch.setattr(dashboard, "CONFIG_FILE", config_file)

    dashboard._save_config({"tunnel_id": "tunnel-test", "password_hash": "hash"})

    assert dashboard._load_config()["tunnel_id"] == "tunnel-test"
    assert "api_key" not in dashboard._load_config()
    assert config_file.stat().st_mode & 0o777 == 0o600
    assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_command_history_merges_start_and_finish_events(tmp_path, monkeypatch) -> None:
    history_file = tmp_path / "command-history.jsonl"
    monkeypatch.setattr(dashboard, "HISTORY_FILE", history_file)
    history_file.write_text(
        "\n".join(
            [
                json.dumps({
                    "id": "abc",
                    "started_at": "2026-11-18T09:00:00Z",
                    "command": "pwd",
                    "status": "running",
                }),
                json.dumps({
                    "id": "abc",
                    "finished_at": "2026-11-18T09:00:01Z",
                    "duration_seconds": 1.0,
                    "exit_code": 0,
                    "status": "success",
                }),
            ]
        ),
        encoding="utf-8",
    )

    result = dashboard._history()

    assert len(result) == 1
    assert result[0]["command"] == "pwd"
    assert result[0]["status"] == "success"
    assert result[0]["exit_code"] == 0



def test_credential_writer_uses_restricted_helper_and_stdin(monkeypatch) -> None:
    captured = {}

    class Result:
        returncode = 0

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(dashboard.subprocess, "run", fake_run)
    dashboard._store_tunnel_credentials("tunnel-example", "runtime-key-example-123456")

    assert captured["command"] == [
        "sudo",
        "-n",
        "-u",
        dashboard.TUNNEL_USER,
        "--",
        dashboard.STORE_HELPER,
        "write",
    ]
    assert captured["kwargs"]["input"] == (
        '{"tunnel_id": "tunnel-example", "api_key": "runtime-key-example-123456"}'
    )
    assert "runtime-key-example-123456" not in " ".join(captured["command"])


def test_status_reports_running_session_uptime(tmp_path, monkeypatch) -> None:
    session_file = tmp_path / "agent-session.json"
    session_file.write_text(
        json.dumps({
            "status": "running",
            "started_at": "2020-01-01T00:00:00+00:00",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "SESSION_FILE", session_file)
    monkeypatch.setattr(dashboard, "_history", lambda: [])
    monkeypatch.setattr(dashboard, "_probe", lambda _url: True)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return json.dumps({
                "status": "ok",
                "details": {"last_success": "now", "consecutive_failures": 0},
            }).encode()

    monkeypatch.setattr(dashboard.urllib.request, "urlopen", lambda *_a, **_k: Response())
    result = dashboard._status()

    assert result["session"]["status"] == "running"
    assert result["uptime_seconds"] > 0


def test_credential_reader_uses_restricted_helper_and_parses_json(monkeypatch) -> None:
    captured = {}

    class Result:
        returncode = 0
        stdout = json.dumps({
            "tunnel_id": "tunnel-example",
            "api_key": "runtime-key-example-123456",
        })

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(dashboard.subprocess, "run", fake_run)
    result = dashboard._read_tunnel_credentials()

    assert result == {
        "tunnel_id": "tunnel-example",
        "api_key": "runtime-key-example-123456",
    }
    assert captured["command"] == [
        "sudo",
        "-n",
        "-u",
        dashboard.TUNNEL_USER,
        "--",
        dashboard.STORE_HELPER,
        "read",
    ]
    assert captured["kwargs"]["timeout"] == 5


def test_credential_reader_rejects_non_object_payload(monkeypatch) -> None:
    class Result:
        returncode = 0
        stdout = "[]"

    monkeypatch.setattr(
        dashboard.subprocess,
        "run",
        lambda *_args, **_kwargs: Result(),
    )

    try:
        dashboard._read_tunnel_credentials()
    except RuntimeError as exc:
        assert "invalid" in str(exc).lower()
    else:
        raise AssertionError("non-object credential payload must be rejected")
