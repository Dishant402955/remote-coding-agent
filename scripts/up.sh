#!/usr/bin/env bash
# Start supervised MCP and Secure MCP Tunnel processes.
set -Eeuo pipefail
umask 077
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
SERVER_LOG="/tmp/remote-coding-agent-server.log"
SERVER_SUPERVISOR_LOG="/tmp/remote-coding-agent-server-supervisor.log"
TUNNEL_CLIENT_LOG="/tmp/remote-coding-agent-tunnel-client.log"
TUNNEL_SUPERVISOR_LOG="/tmp/remote-coding-agent-tunnel-supervisor.log"
MCP_SERVER_URL="http://127.0.0.1:${PORT}/mcp"
HEALTH_URL="http://127.0.0.1:${HEALTH_PORT}"
SERVER_PID=""
TUNNEL_PID=""
CONTROL_PLANE_TUNNEL_ID=""
CONTROL_PLANE_API_KEY=""
export -n CONTROL_PLANE_TUNNEL_ID CONTROL_PLANE_API_KEY 2>/dev/null || true
log() { printf '\n==> %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
port_is_open() { local port="$1"; (echo > "/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1; }
stop_pid() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    for _ in {1..50}; do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
    kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
}
cleanup() {
  local exit_status=$?
  trap - EXIT INT TERM HUP
  if [[ -n "$TUNNEL_PID" ]]; then
    printf '\nStopping isolated tunnel processes. sudo authentication may be requested.\n'
    if sudo -v; then
      sudo -n /usr/bin/pkill -TERM -u "$TUNNEL_USER" 2>/dev/null || true
      sleep 0.5
      sudo -n /usr/bin/pkill -KILL -u "$TUNNEL_USER" 2>/dev/null || true
      stop_pid "$TUNNEL_PID"
      sudo -K 2>/dev/null || true
    else
      printf 'Could not authenticate to stop the isolated tunnel account; stop it manually.\n' >&2
    fi
  fi
  stop_pid "$SERVER_PID"
  unset CONTROL_PLANE_API_KEY CONTROL_PLANE_TUNNEL_ID
  exit "$exit_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
[[ -x "$SERVER" && -x "$PYTHON" ]] || die "Python environment missing. Run scripts/setup-ubuntu.sh first."
[[ -x "$SERVER_SUPERVISOR" ]] || die "Server supervisor missing or not executable."
[[ -x "$TUNNEL_CLIENT" && -x "$TUNNEL_SUPERVISOR" ]] || die "Tunnel-client installation missing. Run scripts/setup-ubuntu.sh first."
command -v curl >/dev/null 2>&1 || die "curl is missing."
command -v setsid >/dev/null 2>&1 || die "setsid is missing."
command -v sudo >/dev/null 2>&1 || die "sudo is required."
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) || die "Invalid MCP port."
[[ "$HEALTH_PORT" =~ ^[0-9]+$ ]] && ((HEALTH_PORT >= 1 && HEALTH_PORT <= 65535)) || die "Invalid health port."
[[ "$PORT" != "$HEALTH_PORT" ]] || die "MCP and health ports must differ."
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
  die "A process is already running as $TUNNEL_USER; inspect it before starting another tunnel."
fi
if port_is_open "$PORT"; then die "MCP port $PORT is already in use."; fi
if port_is_open "$HEALTH_PORT"; then die "Tunnel health port $HEALTH_PORT is already in use."; fi
mkdir -p "$WORKSPACE"
chmod 700 "$WORKSPACE"
read -r -p "Existing Tunnel ID: " CONTROL_PLANE_TUNNEL_ID
read -r -s -p "Runtime API Key: " CONTROL_PLANE_API_KEY
echo
[[ -n "$CONTROL_PLANE_TUNNEL_ID" ]] || die "Tunnel ID is required."
[[ -n "$CONTROL_PLANE_API_KEY" ]] || die "Runtime API key is required."

log "Starting supervised MCP server as $USER"
env -i HOME="$HOME" USER="${USER:-$(id -un)}" PATH="$PATH" LANG="${LANG:-C.UTF-8}" \
  REMOTE_CODING_AGENT_AUTH_MODE=none REMOTE_CODING_AGENT_TEST_MODE=false \
  REMOTE_CODING_AGENT_HOST=127.0.0.1 REMOTE_CODING_AGENT_PORT="$PORT" \
  REMOTE_CODING_AGENT_WORKSPACE="$WORKSPACE" \
  REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS="${REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS:-120}" \
  REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS="${REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS:-3600}" \
  REMOTE_CODING_AGENT_COMMAND_QUEUE_TIMEOUT_SECONDS="${REMOTE_CODING_AGENT_COMMAND_QUEUE_TIMEOUT_SECONDS:-30}" \
  REMOTE_CODING_AGENT_SESSION_IDLE_TIMEOUT_SECONDS="${REMOTE_CODING_AGENT_SESSION_IDLE_TIMEOUT_SECONDS:-86400}" \
  REMOTE_CODING_AGENT_MAX_SESSIONS="${REMOTE_CODING_AGENT_MAX_SESSIONS:-256}" \
  REMOTE_CODING_AGENT_MAX_QUEUED_COMMANDS="${REMOTE_CODING_AGENT_MAX_QUEUED_COMMANDS:-64}" \
  REMOTE_CODING_AGENT_MAX_CONCURRENT_COMMANDS="${REMOTE_CODING_AGENT_MAX_CONCURRENT_COMMANDS:-4}" \
  REMOTE_CODING_AGENT_LOG_FILE="$SERVER_LOG" \
  REMOTE_CODING_AGENT_SUPERVISOR_HEALTH_URL="http://127.0.0.1:${PORT}/" \
  setsid "$SERVER_SUPERVISOR" "$SERVER_SUPERVISOR_LOG" "$SERVER" </dev/null >/dev/null 2>&1 &
SERVER_PID=$!
server_ready=0
for _ in {1..120}; do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    tail -60 "$SERVER_SUPERVISOR_LOG" >&2 || true
    die "MCP server supervisor exited."
  fi
  if port_is_open "$PORT"; then server_ready=1; break; fi
  sleep 0.25
done
[[ "$server_ready" == "1" ]] || {
  tail -60 "$SERVER_LOG" >&2 || true
  tail -60 "$SERVER_SUPERVISOR_LOG" >&2 || true
  die "MCP server did not open port $PORT."
}

log "Checking MCP initialize, tool discovery, and command execution"
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
                {"command": 'test -z "${CONTROL_PLANE_API_KEY:-}" && printf local-mcp-ok'},
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
            if structured.get("stdout") != "local-mcp-ok" or structured.get("exit_code") != 0:
                raise SystemExit(f"MCP smoke test failed: {structured!r}")

asyncio.run(main())
print("MCP handshake and command smoke test passed.")
PY

run_tunnel_client_as_service_user() {
  printf '%s\n' "$CONTROL_PLANE_API_KEY" | sudo -n -u "$TUNNEL_USER" -- /usr/bin/env -i \
    HOME="$TUNNEL_HOME" PATH="$TUNNEL_PATH" LANG="${LANG:-C.UTF-8}" \
    CONTROL_PLANE_TUNNEL_ID="$CONTROL_PLANE_TUNNEL_ID" MCP_SERVER_URL="$MCP_SERVER_URL" \
    HEALTH_LISTEN_ADDR="127.0.0.1:$HEALTH_PORT" \
    "$TUNNEL_SUPERVISOR" "$TUNNEL_CLIENT" "$MCP_SERVER_URL" \
    "$TUNNEL_SUPERVISOR_LOG" "24h" "$HEALTH_URL"
}
log "Starting supervised OpenAI Secure MCP Tunnel as $TUNNEL_USER"
printf 'Tunnel ID: %s\nMCP endpoint: %s\nWorkspace: %s\n' "$CONTROL_PLANE_TUNNEL_ID" "$MCP_SERVER_URL" "$WORKSPACE"
printf 'Tunnel connection TTL: 24h; unhealthy/exited clients are restarted.\n'
printf 'Use tmux: tmux new -s remote-agent ./scripts/up.sh\n'
run_tunnel_client_as_service_user >"$TUNNEL_CLIENT_LOG" 2>&1 &
TUNNEL_PID=$!
CONTROL_PLANE_API_KEY=''
unset CONTROL_PLANE_API_KEY
health_listener_ready=0
for _ in {1..120}; do
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    tail -60 "$TUNNEL_CLIENT_LOG" >&2 || true
    tail -60 "$TUNNEL_SUPERVISOR_LOG" >&2 || true
    die "Tunnel supervisor exited before health listener startup."
  fi
  if curl -fsS --max-time 2 "$HEALTH_URL/healthz" >/dev/null 2>&1; then
    health_listener_ready=1
    break
  fi
  sleep 0.5
done
[[ "$health_listener_ready" == "1" ]] || die "Tunnel health listener did not start."
sudo -K
log "Waiting for tunnel readiness and successful control-plane polling"
tunnel_ready=0
for _ in {1..180}; do
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    tail -60 "$TUNNEL_CLIENT_LOG" >&2 || true
    tail -60 "$TUNNEL_SUPERVISOR_LOG" >&2 || true
    die "Tunnel supervisor exited before reporting readiness."
  fi
  if curl -fsS --max-time 2 "$HEALTH_URL/readyz" >/dev/null 2>&1 \
    && curl -fsS --max-time 2 "$HEALTH_URL/health/control-plane" 2>/dev/null \
      | "$PYTHON" -c 'import json,sys; p=json.load(sys.stdin); d=p.get("details") or {}; sys.exit(0 if p.get("status") == "ok" and d.get("last_success") and d.get("consecutive_failures", 0) == 0 else 1)' 2>/dev/null
  then tunnel_ready=1; break; fi
  sleep 1
done
[[ "$tunnel_ready" == "1" ]] || {
  tail -60 "$TUNNEL_CLIENT_LOG" >&2 || true
  tail -60 "$TUNNEL_SUPERVISOR_LOG" >&2 || true
  die "Tunnel not ready. Check tunnel ID, runtime key permissions, and workspace association."
}
printf '\nSecure MCP Tunnel is ready. Supervised runtime with 24-hour connection TTL.\n'
if wait "$TUNNEL_PID"; then tunnel_status=0; else tunnel_status=$?; fi
TUNNEL_PID=""
exit "$tunnel_status"
