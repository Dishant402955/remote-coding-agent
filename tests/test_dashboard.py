"""Tests for the command-history dashboard."""

from pathlib import Path

from remote_coding_agent import dashboard


def test_dashboard_is_a_command_history_viewer() -> None:
    assert "Command history" in dashboard.PAGE
    assert "new EventSource('/command-events')" in dashboard.PAGE
    assert "function showDetails" in dashboard.PAGE
    assert "Standard output" in dashboard.PAGE
    assert "background:#f5f7fb" in dashboard.PAGE


def test_history_events_merge_updates_and_filter_before_control_plane(tmp_path: Path, monkeypatch) -> None:
    history = tmp_path / "command-history.jsonl"
    connected = tmp_path / "control-plane-connected"
    connected.touch()
    monkeypatch.setattr(dashboard, "HISTORY_FILE", history)
    monkeypatch.setattr(dashboard, "CONNECTED_FILE", connected)
    history.write_text(
        '{"id":"old","started_at":"2000-01-01T00:00:00+00:00","status":"success"}\n'
        '{"id":"new","started_at":"2099-01-01T00:00:00+00:00","status":"running"}\n'
        '{"id":"new","finished_at":"2099-01-01T00:00:01+00:00","status":"success","stdout":"ok"}\n',
        encoding="utf-8",
    )
    events = dashboard._history_events()
    assert len(events) == 1
    assert events[0]["id"] == "new"
    assert events[0]["stdout"] == "ok"


def test_history_events_ignore_malformed_lines(tmp_path: Path, monkeypatch) -> None:
    history = tmp_path / "command-history.jsonl"
    connected = tmp_path / "control-plane-connected"
    connected.touch()
    monkeypatch.setattr(dashboard, "HISTORY_FILE", history)
    monkeypatch.setattr(dashboard, "CONNECTED_FILE", connected)
    history.write_text("not-json\n", encoding="utf-8")
    assert dashboard._history_events() == []
"""