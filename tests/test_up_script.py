"""Regression checks for launcher lock and signal-cleanup wiring."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cleanup_traps_are_installed_only_after_lock_is_acquired() -> None:
    script = (ROOT / "scripts" / "up.sh").read_text(encoding="utf-8")
    lock_marker = (
        'flock -n 9 || die "Another Remote Coding Agent launcher is already running '
        'for this user."'
    )
    lock = script.index(lock_marker)
    trap = script.index("trap cleanup EXIT", lock)
    assert trap > lock
    assert script.index("trap cleanup EXIT") == trap


def test_dashboard_and_server_have_independent_process_groups() -> None:
    script = (ROOT / "scripts" / "up.sh").read_text(encoding="utf-8")
    assert 'setsid env REMOTE_CODING_AGENT_STATE_DIR="$STATE_DIR"' in script
    assert 'setsid "$SERVER_SUPERVISOR"' in script
    assert 'stop_process "$SERVER_PID" supervised' in script
    assert 'stop_process "$DASHBOARD_PID" group' in script
    assert '"$DASHBOARD_LOG" 2>&1 9>&- &' in script
    assert '"$SERVER" 9>&- &' in script
    assert 'run_tunnel_client_as_service_user 9>&- &' in script


def test_interrupt_signals_exit_with_shell_signal_statuses() -> None:
    script = (ROOT / "scripts" / "up.sh").read_text(encoding="utf-8")
    assert "trap 'exit 130' INT" in script
    assert "trap 'exit 143' TERM" in script
    assert "trap 'exit 129' HUP" in script
