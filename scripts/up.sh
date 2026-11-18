#!/usr/bin/env bash
# Start the MCP server and Secure MCP Tunnel with supervised, visible status.
set -Eeuo pipefail
umask 077

# Prevent automatic suspend/idle sleep while the seven-day runtime is active.
if [[ "${REMOTE_CODING_AGENT_INHIBITED:-0}" != "1" ]] && command -v systemd-inhibit >/dev/null 2>&1; then
  export REMOTE_CODING_AGENT_INHIBITED=1
  exec systemd-inhibit \
    --what=sleep:idle:handle-lid-switch \
    --who="Remote Coding Agent" \
    --why="Keep the supervised coding agent available" \
    --mode=block \
    "$BASH" "$0" "$@"
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/.venv/bin/remote-coding-agent"
PYTHON="$ROOT/.venv/bin/python"
SERVER_SUPERVISOR="$ROOT/scripts/server-supervisor.sh"
TUNNEL_USER="remote-coding-agent-tunnel"
TUNNEL_HOME="/var/lib/$TUNNEL_USER"
SYSTEM_BASE="/usr/local/lib/remote-coding-agent"
TUNNEL_CLIENT="$SYSTEM_BASE/tunnel-client/tunnel-client"
TUNNEL_SUPERVISOR="$SYSTEM_BASE/tunnel-client-supervisor.sh"
TUNNEL_PATH="$SYSTEM_BASE/tunnel-client:/usr/bin:/bin"
WORKSPACE="$ROOT/workspace"
PORT="$(printenv REMOTE_CODING_AGENT_PORT || printf '8000')"
HEALTH_PORT="$(printenv REMOTE_CODING_AGENT_HEALTH_PORT || printf '18080')"
DASHBOARD_PORT="$(printenv REMOTE_CODING_AGENT_DASHBOARD_PORT || printf '3333')"
STATE_DIR="${REMOTE_CODING_AGENT_STATE_DIR:-$HOME/.local/state/remote-coding-agent}"
DASHBOARD_LOG="/tmp/remote-coding-agent-dashboard.log"
SERVER_SUPERVISOR_LOG="/tmp/remote-coding-agent-server-supervisor.log"
SERVER_CHILD_LOG="/tmp/remote-coding-agent-server-child.log"
TUNNEL_CLIENT_LOG="/tmp/remote-coding-agent-tunnel-client.log"
TUNNEL_SUPERVISOR_LOG="/tmp/remote-coding-agent-tunnel-supervisor.log"
MCP_SERVER_URL="http://127.0.0.1:${PORT}/mcp"
MCP_READY_URL="http://127.0.0.1:${PORT}/readyz"
HEALTH_URL="http://127.0.0.1:${HEALTH_PORT}"
SERVER_PID=""
TUNNEL_PID=""
DASHBOARD_PID=""
CONTROL_PLANE_TUNNEL_ID=""
CONTROL_PLANE_API_KEY=""
STATE="STOPPED"
export -n CONTROL_PLANE_TUNNEL_ID CONTROL_PLANE_API_KEY 2>/dev/null || true

status() {
  local next="$1"; shift
  [[ "$STATE" != "$next" ]] || return 0
  printf '%s | STATUS: %s | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$next" "$*"
  STATE="$next"
}
info() { printf '%s | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { printf '%s | STATUS: FAILED | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; exit 1; }
port_is_open() { local port="$1"; (echo > "/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1; }

stop_process() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    for _ in {1..50}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
}

cleanup() {
  local exit_status=$?
  trap - EXIT INT TERM HUP
  status STOPPING "shutting down managed processes"
  cleanup_complete=1

  if [[ -n "$TUNNEL_PID" ]]; then
    if sudo -v; then
      sudo -n /usr/bin/pkill -TERM -u "$TUNNEL_USER" 2>/dev/null || true
      tunnel_stopped=0
      for _ in {1..100}; do
        if ! sudo -n /usr/bin/pgrep -u "$TUNNEL_USER" >/dev/null 2>&1; then
          tunnel_stopped=1
          break
        fi
        sleep 0.1
      done
      if ((tunnel_stopped == 0)); then
        info "Tunnel did not stop within 10 seconds; forcing remaining tunnel processes to exit"
        sudo -n /usr/bin/pkill -KILL -u "$TUNNEL_USER" 2>/dev/null || true
        sleep 0.2
        if sudo -n /usr/bin/pgrep -u "$TUNNEL_USER" >/dev/null 2>&1; then
          cleanup_complete=0
        fi
      fi
      stop_process "$TUNNEL_PID"
      sudo -K 2>/dev/null || true
    else
      printf 'STATUS: WARNING | Could not authenticate to stop tunnel service account %s.\n' "$TUNNEL_USER" >&2
      cleanup_complete=0
    fi
  fi

  stop_process "$SERVER_PID"
  stop_process "$DASHBOARD_PID"
  unset CONTROL_PLANE_API_KEY CONTROL_PLANE_TUNNEL_ID
  sudo -K 2>/dev/null || true
  if ((cleanup_complete)); then
    status STOPPED "server and tunnel process trees stopped (exit=$exit_status)"
  else
    status STOPPED "server stopped; tunnel cleanup still needs sudo for $TUNNEL_USER"
  fi
  exit "$exit_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

[[ -t 0 && -t 1 ]] || die "Run this script in an interactive terminal (tmux is recommended)."
command -v flock >/dev/null 2>&1 || die "flock is missing. Install util-linux and retry."
LOCK_DIR="$HOME/.cache/remote-coding-agent"
mkdir -p "$LOCK_DIR"
chmod 700 "$LOCK_DIR"
exec 9>"$LOCK_DIR/up.lock"
flock -n 9 || die "Another Remote Coding Agent launcher is already running for this user."

[[ -x "$SERVER" && -x "$PYTHON" ]] || die "Python environment missing. Run scripts/setup-ubuntu.sh first."
[[ -x "$SERVER_SUPERVISOR" ]] || die "Server supervisor missing or not executable."
[[ -x "$TUNNEL_CLIENT" && -x "$TUNNEL_SUPERVISOR" ]] || die "Tunnel-client installation missing. Run scripts/setup-ubuntu.sh first."
command -v curl >/dev/null 2>&1 || die "curl is missing."
command -v setsid >/dev/null 2>&1 || die "setsid is missing."
command -v sudo >/dev/null 2>&1 || die "sudo is required."
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) || die "Invalid MCP port."
[[ "$HEALTH_PORT" =~ ^[0-9]+$ ]] && ((HEALTH_PORT >= 1 && HEALTH_PORT <= 65535)) || die "Invalid tunnel health port."
[[ "$DASHBOARD_PORT" =~ ^[0-9]+$ ]] && ((DASHBOARD_PORT >= 1 && DASHBOARD_PORT <= 65535)) || die "Invalid dashboard port."
[[ "$PORT" != "$HEALTH_PORT" && "$PORT" != "$DASHBOARD_PORT" && "$HEALTH_PORT" != "$DASHBOARD_PORT" ]] || die "MCP, dashboard, and tunnel health ports must differ."

ACCOUNT_ENTRY="$(getent passwd "$TUNNEL_USER")" || die "Tunnel service account missing; rerun setup."
IFS=: read -r _ _ ACCOUNT_UID _ _ ACCOUNT_HOME ACCOUNT_SHELL <<< "$ACCOUNT_ENTRY"
[[ "$ACCOUNT_HOME" == "$TUNNEL_HOME" && "$ACCOUNT_SHELL" == "/usr/sbin/nologin" ]] || die "Tunnel service account is misconfigured; rerun setup."
((ACCOUNT_UID < 1000)) || die "Tunnel service account is not a system account."
for path in "$SYSTEM_BASE" "$(dirname "$TUNNEL_CLIENT")"; do
  [[ "$(stat -c '%U:%G:%a' "$path")" == "root:root:755" ]] || die "$path must be root:root mode 755; rerun setup."
done

sudo -v
sudo -n -u "$TUNNEL_USER" -- /usr/bin/test -x "$TUNNEL_CLIENT" || die "Tunnel account cannot execute tunnel-client."
sudo -n -u "$TUNNEL_USER" -- "$TUNNEL_CLIENT" --version >/dev/null || die "Tunnel client failed version check."
if sudo -n /usr/bin/pgrep -u "$TUNNEL_USER" >/dev/null 2>&1; then
  die "A tunnel process is already running as $TUNNEL_USER. Inspect it before starting another tunnel."
fi
if port_is_open "$PORT"; then die "MCP port $PORT is already in use. Stop the existing server first."; fi
if port_is_open "$HEALTH_PORT"; then die "Tunnel health port $HEALTH_PORT is already in use."; fi
if port_is_open "$DASHBOARD_PORT"; then die "Dashboard port $DASHBOARD_PORT is already in use."; fi

mkdir -p "$WORKSPACE" "$STATE_DIR"
chmod 700 "$WORKSPACE" "$STATE_DIR"
: > "$DASHBOARD_LOG"
chmod 600 "$DASHBOARD_LOG"
env REMOTE_CODING_AGENT_STATE_DIR="$STATE_DIR" \
  REMOTE_CODING_AGENT_DASHBOARD_PORT="$DASHBOARD_PORT" \
  REMOTE_CODING_AGENT_PORT="$PORT" \
  REMOTE_CODING_AGENT_HEALTH_PORT="$HEALTH_PORT" \
  "$PYTHON" -m remote_coding_agent.dashboard >>"$DASHBOARD_LOG" 2>&1 &
DASHBOARD_PID=$!
dashboard_ready=0
for _ in {1..80}; do
  if ! kill -0 "$DASHBOARD_PID" 2>/dev/null; then
    tail -30 "$DASHBOARD_LOG" >&2 || true
    die "Dashboard exited during startup."
  fi
  if curl -fsS --max-time 1 "http://127.0.0.1:${DASHBOARD_PORT}/" >/dev/null 2>&1; then
    dashboard_ready=1
    break
  fi
  sleep 0.25
done
[[ "$dashboard_ready" == "1" ]] || die "Dashboard did not become ready within 20 seconds."
status STARTING "waiting for secure dashboard setup"
printf 'Open the local dashboard: http://127.0.0.1:%s\n' "$DASHBOARD_PORT"
printf 'Create a dashboard password and enter your existing Tunnel ID + Runtime API Key.\n'
"$PYTHON" -m remote_coding_agent.dashboard --wait-for-config \
  --timeout-seconds "${REMOTE_CODING_AGENT_DASHBOARD_SETUP_TIMEOUT_SECONDS:-3600}" || die "Dashboard setup was not completed."
mapfile -t CREDENTIALS < <("$PYTHON" -m remote_coding_agent.dashboard --read-credentials) || die "Could not read dashboard credentials."
CONTROL_PLANE_TUNNEL_ID="${CREDENTIALS[0]:-}"
CONTROL_PLANE_API_KEY="${CREDENTIALS[1]:-}"
unset CREDENTIALS
[[ -n "$CONTROL_PLANE_TUNNEL_ID" && -n "$CONTROL_PLANE_API_KEY" ]] || die "Dashboard credentials are incomplete."

status STARTING "preparing MCP server"
: > "$SERVER_SUPERVISOR_LOG"
: > "$SERVER_CHILD_LOG"
chmod 600 "$SERVER_SUPERVISOR_LOG" "$SERVER_CHILD_LOG"

# Keep a seven-day idle MCP session window and bounded resource usage.
env -i \
  HOME="$HOME" USER="${USER:-$(id -un)}" PATH="$PATH" LANG="${LANG:-C.UTF-8}" \
  REMOTE_CODING_AGENT_AUTH_MODE=none \
  REMOTE_CODING_AGENT_TEST_MODE=false \
  REMOTE_CODING_AGENT_HOST=127.0.0.1 \
  REMOTE_CODING_AGENT_PORT="$PORT" \
  REMOTE_CODING_AGENT_WORKSPACE="$WORKSPACE" \
  REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS="${REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS:-120}" \
  REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS="${REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS:-3600}" \
  REMOTE_CODING_AGENT_COMMAND_QUEUE_TIMEOUT_SECONDS="${REMOTE_CODING_AGENT_COMMAND_QUEUE_TIMEOUT_SECONDS:-30}" \
  REMOTE_CODING_AGENT_SESSION_IDLE_TIMEOUT_SECONDS="${REMOTE_CODING_AGENT_SESSION_IDLE_TIMEOUT_SECONDS:-604800}" \
  REMOTE_CODING_AGENT_MAX_SESSIONS="${REMOTE_CODING_AGENT_MAX_SESSIONS:-256}" \
  REMOTE_CODING_AGENT_MAX_QUEUED_COMMANDS="${REMOTE_CODING_AGENT_MAX_QUEUED_COMMANDS:-64}" \
  REMOTE_CODING_AGENT_MAX_CONCURRENT_COMMANDS="${REMOTE_CODING_AGENT_MAX_CONCURRENT_COMMANDS:-4}" \
  REMOTE_CODING_AGENT_CHILD_LOG_FILE="$SERVER_CHILD_LOG" \
  REMOTE_CODING_AGENT_SUPERVISOR_HEALTH_URL="http://127.0.0.1:${PORT}/readyz" \
  setsid "$SERVER_SUPERVISOR" "$SERVER_SUPERVISOR_LOG" "$SERVER" &
SERVER_PID=$!

server_ready=0
for _ in {1..120}; do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    tail -30 "$SERVER_CHILD_LOG" >&2 || true
    die "MCP server supervisor exited during startup."
  fi
  if curl -fsS --max-time 2 "$MCP_READY_URL" >/dev/null 2>&1; then
    server_ready=1
    break
  fi
  sleep 0.25
done
[[ "$server_ready" == "1" ]] || {
  tail -30 "$SERVER_CHILD_LOG" >&2 || true
  die "MCP server did not become ready within 30 seconds."
}

info "MCP server readiness endpoint passed; checking the MCP protocol and run_command tool"
MCP_SERVER_URL="$MCP_SERVER_URL" "$PYTHON" - <<'PY'
import asyncio
import json
import os
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main() -> None:
    async with streamablehttp_client(os.environ["MCP_SERVER_URL"]) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            available = await session.list_tools()
            if "run_command" not in {tool.name for tool in available.tools}:
                raise SystemExit("run_command is not registered")
            result = await session.call_tool(
                "run_command",
                {"command": 'test -z "${CONTROL_PLANE_API_KEY:-}" && printf mcp-ok'},
            )
            if result.isError:
                raise SystemExit("run_command returned an MCP error")
            structured = getattr(result, "structuredContent", None)
            if not structured:
                for item in result.content:
                    try:
                        candidate = json.loads(getattr(item, "text", ""))
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(candidate, dict) and "stdout" in candidate:
                        structured = candidate
                        break
            if not isinstance(structured, dict):
                raise SystemExit("run_command returned no structured command result")
            if structured.get("stdout") != "mcp-ok" or structured.get("exit_code") != 0:
                raise SystemExit(f"MCP smoke test failed: {structured!r}")

asyncio.run(main())
PY
info "MCP protocol smoke test passed"

run_tunnel_client_as_service_user() {
  printf '%s\n' "$CONTROL_PLANE_API_KEY" | sudo -n -u "$TUNNEL_USER" -- /usr/bin/env -i \
    HOME="$TUNNEL_HOME" \
    PATH="$TUNNEL_PATH" \
    LANG="${LANG:-C.UTF-8}" \
    CONTROL_PLANE_TUNNEL_ID="$CONTROL_PLANE_TUNNEL_ID" \
    MCP_SERVER_URL="$MCP_SERVER_URL" \
    HEALTH_LISTEN_ADDR="127.0.0.1:$HEALTH_PORT" \
    REMOTE_CODING_AGENT_TUNNEL_CLIENT_LOG="$TUNNEL_CLIENT_LOG" \
    REMOTE_CODING_AGENT_TUNNEL_HEALTH_INTERVAL_SECONDS=10 \
    REMOTE_CODING_AGENT_TUNNEL_STARTUP_GRACE_SECONDS=180 \
    "$TUNNEL_SUPERVISOR" "$TUNNEL_CLIENT" "$MCP_SERVER_URL" \
    "$TUNNEL_SUPERVISOR_LOG" "24h" "$HEALTH_URL"
}

status STARTING "connecting Secure MCP Tunnel (maximum MCP connection TTL: 24h)"
sudo install -o "$TUNNEL_USER" -g "$TUNNEL_USER" -m 600 /dev/null "$TUNNEL_CLIENT_LOG"
sudo install -o "$TUNNEL_USER" -g "$TUNNEL_USER" -m 600 /dev/null "$TUNNEL_SUPERVISOR_LOG"
run_tunnel_client_as_service_user &
TUNNEL_PID=$!
CONTROL_PLANE_API_KEY=''
unset CONTROL_PLANE_API_KEY

health_listener_ready=0
for _ in {1..120}; do
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    info "See tunnel diagnostics with: sudo tail -40 $TUNNEL_CLIENT_LOG"
    info "Supervisor status log: $TUNNEL_SUPERVISOR_LOG"
    die "Tunnel supervisor exited before health listener startup."
  fi
  if curl -fsS --max-time 2 "$HEALTH_URL/healthz" >/dev/null 2>&1; then
    health_listener_ready=1
    break
  fi
  sleep 0.5
done
[[ "$health_listener_ready" == "1" ]] || die "Tunnel health listener did not start within 60 seconds."
info "Tunnel health endpoint is responding; waiting for control-plane registration"
sudo -K

startup_timeout="${REMOTE_CODING_AGENT_STARTUP_TIMEOUT_SECONDS:-300}"
[[ "$startup_timeout" =~ ^[0-9]+$ ]] && ((startup_timeout >= 30 && startup_timeout <= 900)) || die "REMOTE_CODING_AGENT_STARTUP_TIMEOUT_SECONDS must be between 30 and 900."
tunnel_ready=0
last_wait_notice=0
for ((elapsed=0; elapsed<startup_timeout; elapsed++)); do
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    info "See tunnel diagnostics with: sudo tail -40 $TUNNEL_CLIENT_LOG"
    info "Supervisor status log: $TUNNEL_SUPERVISOR_LOG"
    die "Tunnel supervisor exited before reporting readiness."
  fi
  if curl -fsS --max-time 2 "$HEALTH_URL/readyz" >/dev/null 2>&1 \
    && curl -fsS --max-time 2 "$HEALTH_URL/health/control-plane" 2>/dev/null \
      | "$PYTHON" -c 'import json,sys; p=json.load(sys.stdin); d=p.get("details") or {}; sys.exit(0 if p.get("status") == "ok" and d.get("last_success") and d.get("consecutive_failures", 0) == 0 else 1)' 2>/dev/null
  then
    tunnel_ready=1
    break
  fi
  if ((elapsed - last_wait_notice >= 15)); then
    info "Still waiting for OpenAI control-plane registration (${elapsed}s elapsed)"
    last_wait_notice=$elapsed
  fi
  sleep 1
done
[[ "$tunnel_ready" == "1" ]] || {
  info "See tunnel diagnostics with: sudo tail -40 $TUNNEL_CLIENT_LOG"
  info "Supervisor status log: $TUNNEL_SUPERVISOR_LOG"
  die "Tunnel did not become ready within ${startup_timeout}s. Check tunnel ID, key permissions, and workspace association."
}

status RUNNING "MCP server and Secure MCP Tunnel are healthy"
printf 'Dashboard: http://127.0.0.1:%s\n' "$DASHBOARD_PORT"
printf 'Console stays attached. Detach tmux with Ctrl+B, then D.\n'
printf 'Reconnect with: tmux attach -t remote-agent\n'
printf 'Server logs: %s | %s\n' "$SERVER_CHILD_LOG" "$SERVER_SUPERVISOR_LOG"
printf 'Dashboard log: %s\n' "$DASHBOARD_LOG"
printf 'Tunnel logs (sudo required): %s | %s\n' "$TUNNEL_CLIENT_LOG" "$TUNNEL_SUPERVISOR_LOG"

if wait "$TUNNEL_PID"; then tunnel_status=0; else tunnel_status=$?; fi
die "Tunnel supervisor exited unexpectedly (code=$tunnel_status)."
