"""Tests for the minimal real-time log dashboard."""

from pathlib import Path

from remote_coding_agent import dashboard


def test_dashboard_is_a_black_and_white_live_log_viewer() -> None:
    assert "background:#000" in dashboard.PAGE
    assert "color:#fff" in dashboard.PAGE
    assert "new EventSource('/events')" in dashboard.PAGE
    assert "Start agent" not in dashboard.PAGE
    assert "Runtime API Key" not in dashboard.PAGE


def test_log_stream_resumes_from_last_delivered_offset(tmp_path: Path) -> None:
    log_file = tmp_path / "live.log"
    log_file.write_text("first\nsecond\nthird\n", encoding="utf-8")

    assert dashboard._initial_offset(log_file, "6") == 6
    assert dashboard._initial_offset(log_file, "not-an-offset") == 0


def test_new_log_stream_starts_at_a_line_boundary(tmp_path: Path) -> None:
    log_file = tmp_path / "live.log"
    log_file.write_text("x" * 70_000 + "\nlast line\n", encoding="utf-8")

    offset = dashboard._initial_offset(log_file, None)

    assert offset > 0
    with log_file.open("rb") as stream:
        stream.seek(offset)
        assert stream.readline() == b"last line\n"
