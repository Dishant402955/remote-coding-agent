#!/usr/bin/env bash
# Start the native MCP server and connect it to an existing OpenAI Secure MCP Tunnel.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/.venv/bin/remote-coding-agent"
PYTHON="$ROOT/.venv/bin/python"
TUNNEL_USER="remote-coding-agent-tunnel"
TUNNEL_HOME="/var/lib/$TUNNEL_USER"
TUNNEL_CLIENT="/usr/local/lib/remote-coding-agent/tunnel-client/tunnel-client"
TUNNEL_PATH="/usr/local/lib/remote-coding-agent/tunnel-client:/usr/bin:/bin"
WORKSPACE="$ROOT/workspace"
PORT="${REMOTE_CODING_AGENT_PORT:-8000}"
HEALTH_PORT="${REMOTE_CODING_AGENT_HEALTH_PORT:-18080}"
SERVER_LOG="/tmp/remote-coding-agent-server.log"
MCP_SERVER_URL="http://127.0.0.1:${PORT}/mcp"
HEALTH_URL="http://127.0.0.1:${HEALTH_PORT}"
SERVER_PID=""
TUNNEL_PID=""
CONTROL_PLANE_TUNNEL_ID=""
CONTROL_PLANE_API_KEY=""
# Do not inherit an exported key from the calling shell; read it as a private
# shell variable and transfer it to the service UID through stdin only.
export -n CONTROL_PLANE_TUNNEL_ID CONTROL_PLANE_API_KEY 2>/dev/null || true

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
port_is_open() {
  local port="$1"
  (echo > "/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1
}
stop_pid() {
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
  trap - EXIT INT TERM
  if [[ -n "$TUNNEL_PID" ]]; then
    printf '\nStopping the isolated tunnel process. sudo authentication may be requested.\n'
    if sudo -v; then
      sudo -n /usr/bin/pkill -TERM -u "$TUNNEL_USER" 2>/dev/null || true
      sleep 0.5
      sudo -n /usr/bin/pkill -KILL -u "$TUNNEL_USER" 2>/dev/null || true
      stop_pid "$TUNNEL_PID"
      sudo -K 2>/dev/null || true
    else
      printf 'Warning: could not authenticate to stop processes owned by %s. Stop them manually with sudo.\n' "$TUNNEL_USER" >&2
    fi
  fi
  stop_pid "$SERVER_PID"
  unset CONTROL_PLANE_API_KEY CONTROL_PLANE_TUNNEL_ID
  exit "$exit_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ -x "$SERVER" && -x "$PYTHON" ]] || die "Python environment missing. Run scripts/setup-ubuntu.sh first."
[[ -x "$TUNNEL_CLIENT" ]] || die "Dedicated tunnel-client installation missing. Run scripts/setup-ubuntu.sh first."
command -v curl >/dev/null 2>&1 || die "curl is missing. Run scripts/setup-ubuntu.sh first."
command -v setsid >/dev/null 2>&1 || die "setsid is missing. Install util-linux and retry."
command -v sudo >/dev/null 2>&1 || die "sudo is required to start the isolated tunnel-client account."
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) || die "REMOTE_CODING_AGENT_PORT must be between 1 and 65535."
[[ "$HEALTH_PORT" =~ ^[0-9]+$ ]] && ((HEALTH_PORT >= 1 && HEALTH_PORT <= 65535)) || die "REMOTE_CODING_AGENT_HEALTH_PORT must be between 1 and 65535."
[[ "$PORT" != "$HEALTH_PORT" ]] || die "MCP and tunnel health ports must be different."

ACCOUNT_ENTRY="$(getent passwd "$TUNNEL_USER")" || die "Tunnel service account is missing. Run scripts/setup-ubuntu.sh first."
IFS=: read -r _ _ ACCOUNT_UID _ _ ACCOUNT_HOME ACCOUNT_SHELL <<< "$ACCOUNT_ENTRY"
[[ "$ACCOUNT_HOME" == "$TUNNEL_HOME" && "$ACCOUNT_SHELL" == "/usr/sbin/nologin" ]] || die "Tunnel service account is not configured as the expected locked account. Rerun scripts/setup-ubuntu.sh."
((ACCOUNT_UID < 1000)) || die "Tunnel service account is not a system account. Rerun scripts/setup-ubuntu.sh."
if [[ "$(stat -c '%U:%G:%a' "$(dirname "$TUNNEL_CLIENT")")" != "root:root:755" ]]; then
  die "Tunnel-client installation directory must be root:root mode 755. Rerun scripts/setup-ubuntu.sh."
fi
# Authenticate before reading the API key. The ticket is invalidated again as
# soon as the dedicated tunnel process has started listening on loopback.
sudo -v
sudo -n -u "$TUNNEL_USER" -- /usr/bin/test -x "$TUNNEL_CLIENT" || die "Dedicated tunnel account cannot execute tunnel-client."
sudo -n -u "$TUNNEL_USER" -- "$TUNNEL_CLIENT" --version >/dev/null || die "Dedicated tunnel account could not start tunnel-client."
if sudo -n /usr/bin/pgrep -u "$TUNNEL_USER" >/dev/null 2>&1; then
  die "A process is already running as $TUNNEL_USER. Inspect it before starting another tunnel."
fi
if port_is_open "$PORT"; then
  die "MCP port $PORT is already accepting connections. Stop the existing service or choose another REMOTE_CODING_AGENT_PORT."
fi
if port_is_open "$HEALTH_PORT"; then
  die "Tunnel health port $HEALTH_PORT is already accepting connections. Choose another REMOTE_CODING_AGENT_HEALTH_PORT."
fi

mkdir -p "$WORKSPACE"
chmod 700 "$WORKSPACE"
read -r -p "Existing Tunnel ID: " CONTROL_PLANE_TUNNEL_ID
read -r -s -p "Runtime API Key: " CONTROL_PLANE_API_KEY
echo
[[ -n "$CONTROL_PLANE_TUNNEL_ID" ]] || die "Tunnel ID is required."
[[ -n "$CONTROL_PLANE_API_KEY" ]] || die "Runtime API key is required."

log "Starting local MCP server as $USER"
# The MCP process and its commands receive no tunnel credential.
env -i \
  HOME="$HOME" \
  USER="${USER:-$(id -un)}" \
  PATH="$PATH" \
  LANG="${LANG:-C.UTF-8}" \
  REMOTE_CODING_AGENT_AUTH_MODE=none \
  REMOTE_CODING_AGENT_TEST_MODE=false \
  REMOTE_CODING_AGENT_HOST=127.0.0.1 \
  REMOTE_CODING_AGENT_PORT="$PORT" \
  REMOTE_CODING_AGENT_WORKSPACE="$WORKSPACE" \
  setsid "$SERVER" </dev/null >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

server_ready=0
for _ in {1..60}; do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "MCP server exited. Recent log:" >&2
    tail -60 "$SERVER_LOG" >&2 || true
    die "MCP server failed to start."
  fi
  if port_is_open "$PORT"; then
    server_ready=1
    break
  fi
  sleep 0.25
done
[[ "$server_ready" == "1" ]] || { tail -60 "$SERVER_LOG" >&2 || true; die "MCP server did not open port $PORT."; }

log "Checking MCP initialization, tool discovery, and command execution"
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
                raise SystemExit("MCP server did not register run_command")
            result = await session.call_tool(
                "run_command", {
                    "command": 'test -z "${CONTROL_PLANE_API_KEY:-}" && printf local-mcp-ok'
                }
            )
            if result.isError:
                raise SystemExit("run_command returned an MCP error")
            structured = getattr(result, "structuredContent", None)
            if not structured:
                for item in result.content:
                    text = getattr(item, "text", "")
                    try:
                        candidate = json.loads(text)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(candidate, dict) and "stdout" in candidate:
                        structured = candidate
                        break
            if not isinstance(structured, dict):
                raise SystemExit("run_command did not return structured command output")
            if structured.get("stdout") != "local-mcp-ok" or structured.get("exit_code") != 0:
                raise SystemExit(f"run_command smoke test failed: {structured!r}")

asyncio.run(main())
print("MCP handshake passed; run_command smoke test passed.")
PY

# The API key is piped to a shell running as the dedicated tunnel UID. It is
# absent from this user's process argv/environment and never reaches MCP.
run_tunnel_client_as_service_user() {
  printf '%s\n' "$CONTROL_PLANE_API_KEY" | sudo -n -u "$TUNNEL_USER" -- /usr/bin/env -i \
    HOME="$TUNNEL_HOME" \
    PATH="$TUNNEL_PATH" \
    LANG="${LANG:-C.UTF-8}" \
    CONTROL_PLANE_TUNNEL_ID="$CONTROL_PLANE_TUNNEL_ID" \
    MCP_SERVER_URL="$MCP_SERVER_URL" \
    HEALTH_LISTEN_ADDR="127.0.0.1:$HEALTH_PORT" \
    /bin/bash -c '
      IFS= read -r CONTROL_PLANE_API_KEY || exit 1
      export CONTROL_PLANE_API_KEY
      "$1" doctor --explain || exit $?
      exec "$1" run
    ' _ "$TUNNEL_CLIENT"
}

log "Starting OpenAI Secure MCP Tunnel as $TUNNEL_USER"
printf 'Tunnel ID: %s\n' "$CONTROL_PLANE_TUNNEL_ID"
printf 'MCP endpoint: %s\n' "$MCP_SERVER_URL"
printf 'Workspace (default working directory): %s\n' "$WORKSPACE"
printf 'Tunnel health endpoint: %s\n' "$HEALTH_URL"
printf 'Server log: %s\n' "$SERVER_LOG"
printf 'Keep this terminal open. Press Ctrl+C to stop the tunnel and MCP server.\n\n'
run_tunnel_client_as_service_user &
TUNNEL_PID=$!
# The background launcher has its own copy for the stdin handoff. Erase the
# parent shell's copy immediately; it is not needed after the tunnel starts.
CONTROL_PLANE_API_KEY=''
unset CONTROL_PLANE_API_KEY

# Wait for the service user's tunnel process to expose its loopback health UI.
health_listener_ready=0
for _ in {1..60}; do
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    die "tunnel-client diagnostics failed or tunnel-client exited before starting."
  fi
  if curl -fsS --max-time 2 "$HEALTH_URL/healthz" >/dev/null 2>&1; then
    health_listener_ready=1
    break
  fi
  sleep 0.5
done
[[ "$health_listener_ready" == "1" ]] || die "Tunnel health listener did not start on $HEALTH_URL."

# Remove all cached sudo credentials now, before the remote tunnel reports ready.
# MCP commands cannot silently reuse the launcher's sudo authorization.
sudo -K

log "Waiting for tunnel readiness and successful OpenAI control-plane polling"
tunnel_ready=0
for _ in {1..120}; do
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    die "tunnel-client exited before reporting readiness."
  fi
  if curl -fsS --max-time 2 "$HEALTH_URL/readyz" >/dev/null 2>&1 \
    && curl -fsS --max-time 2 "$HEALTH_URL/health/control-plane" 2>/dev/null \
      | "$PYTHON" -c 'import json,sys; payload=json.load(sys.stdin); details=payload.get("details") or {}; sys.exit(0 if payload.get("status") == "ok" and details.get("last_success") and details.get("consecutive_failures", 0) == 0 else 1)' 2>/dev/null
  then
    tunnel_ready=1
    break
  fi
  sleep 1
done
[[ "$tunnel_ready" == "1" ]] || die "Tunnel did not report ready within 120 seconds."
printf '\nSecure MCP Tunnel is ready; local health and successful control-plane polling verified.\nHealth endpoint: %s\n' "$HEALTH_URL"
printf 'The sudo timestamp was cleared. Stopping this session will ask for your sudo password again.\n'

if wait "$TUNNEL_PID"; then
  tunnel_status=0
else
  tunnel_status=$?
fi
TUNNEL_PID=""
exit "$tunnel_status"
