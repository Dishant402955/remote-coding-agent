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

    dashboard._save_config({"tunnel_id": "tunnel-test", "api_key": "test-runtime-key"})

    assert dashboard._load_config()["tunnel_id"] == "tunnel-test"
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
