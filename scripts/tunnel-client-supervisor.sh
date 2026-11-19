#!/usr/bin/env bash
# Keep Secure MCP Tunnel alive; the runtime key arrives over stdin, never argv.
set -Eeuo pipefail
umask 077

# Never retain descriptors owned by the foreground launcher.
exec 9>&-

TUNNEL_CLIENT="${1:?usage: tunnel-client-supervisor.sh CLIENT MCP_URL STATUS_LOG TTL HEALTH_URL}"
MCP_SERVER_URL="${2:?MCP URL is required}"
LOG_FILE="${3:?supervisor status log is required}"
CONNECTION_MAX_TTL="${4:-24h}"
HEALTH_URL="${5:-}"
CLIENT_LOG="${REMOTE_CODING_AGENT_TUNNEL_CLIENT_LOG:-${LOG_FILE%.log}-client.log}"
IFS= read -r CONTROL_PLANE_API_KEY || {
  printf 'STATUS: FAILED | tunnel key was not provided on stdin\n' >&2
  exit 2
}
[[ -n "$CONTROL_PLANE_API_KEY" ]] || {
  printf 'STATUS: FAILED | tunnel key was empty\n' >&2
  exit 2
}
export CONTROL_PLANE_API_KEY MCP_SERVER_URL

HEALTH_INTERVAL="${REMOTE_CODING_AGENT_TUNNEL_HEALTH_INTERVAL_SECONDS:-10}"
HEALTH_FAILURE_LIMIT="${REMOTE_CODING_AGENT_TUNNEL_HEALTH_FAILURE_LIMIT:-3}"
STARTUP_GRACE="${REMOTE_CODING_AGENT_TUNNEL_STARTUP_GRACE_SECONDS:-180}"
STATE="STOPPED"
CHILD_PID=""
MONITOR_PID=""

rotate_status_log() {
  if [[ -f "$LOG_FILE" ]] && (( $(stat -c '%s' "$LOG_FILE") >= 1048576 )); then
    mv -f -- "$LOG_FILE" "$LOG_FILE.1"
  fi
}

rotate_client_log() {
  [[ -f "$CLIENT_LOG" ]] || return 0
  (( $(stat -c '%s' "$CLIENT_LOG") >= 10485760 )) || return 0
  for index in 3 2; do
    previous=$((index - 1))
    [[ -f "$CLIENT_LOG.$previous" ]] && cp -f -- "$CLIENT_LOG.$previous" "$CLIENT_LOG.$index"
  done
  cp -f -- "$CLIENT_LOG" "$CLIENT_LOG.1"
  : > "$CLIENT_LOG"
  chmod 600 "$CLIENT_LOG" "$CLIENT_LOG.1"
}

transition() {
  local next="$1"; shift
  [[ "$STATE" != "$next" ]] || return 0
  rotate_status_log
  local line
  line="$(date '+%Y-%m-%d %H:%M:%S') | STATUS: $next | Secure MCP Tunnel | $*"
  printf '%s\n' "$line" >> "$LOG_FILE"
  printf '%s\n' "$line"
  STATE="$next"
}

check_health() {
  [[ -n "$HEALTH_URL" ]] || return 0
  curl -fsS --max-time 5 "$HEALTH_URL/healthz" >/dev/null 2>&1 || return 1
  curl -fsS --max-time 5 "$HEALTH_URL/readyz" >/dev/null 2>&1 || return 1
  curl -fsS --max-time 5 "$HEALTH_URL/health/control-plane" 2>/dev/null |
    python3 -c '
import json
import sys
payload = json.load(sys.stdin)
details = payload.get("details") or {}
sys.exit(0 if payload.get("status") == "ok"
         and details.get("last_success")
         and details.get("consecutive_failures", 0) == 0 else 1)
' || return 1
}

stop_monitor() {
  [[ -n "$MONITOR_PID" ]] || return 0
  kill -TERM "$MONITOR_PID" 2>/dev/null || true
  wait "$MONITOR_PID" 2>/dev/null || true
  MONITOR_PID=""
}

stop_child() {
  [[ -n "$CHILD_PID" ]] || return 0
  kill -TERM -- "-$CHILD_PID" 2>/dev/null || true
  for _ in {1..50}; do
    local child_state
    child_state="$(ps -o stat= -p "$CHILD_PID" 2>/dev/null || true)"
    child_state="${child_state//[[:space:]]/}"
    [[ -n "$child_state" && "$child_state" != Z* ]] || break
    sleep 0.1
  done
  kill -KILL -- "-$CHILD_PID" 2>/dev/null || true
  wait "$CHILD_PID" 2>/dev/null || true
  CHILD_PID=""
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM HUP
  transition STOPPING "shutdown requested"
  stop_monitor
  stop_child
  unset CONTROL_PLANE_API_KEY
  transition STOPPED "tunnel process tree stopped (exit=$exit_code)"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

rotate_client_log
transition STARTING "checking tunnel-client installation"
if ! "$TUNNEL_CLIENT" doctor --explain 2>&1 | tee -a "$CLIENT_LOG"; then
  transition DEGRADED "preflight diagnostics reported a warning; attempting connection"
fi
backoff=2
while true; do
  started=$SECONDS
  health_ready=0
  health_failures=0
  transition STARTING "connecting (maximum connection TTL: $CONNECTION_MAX_TTL)"
  rotate_client_log
  # Tee tunnel-client output to both its private log and the launcher's live log stream.
  # The wrapper and its pipeline stay in one process group for reliable cleanup.
  setsid bash -o pipefail -c '"$@" 2>&1 | tee -a "$REMOTE_CODING_AGENT_TUNNEL_CLIENT_LOG"' _ \
    "$TUNNEL_CLIENT" run "--mcp.connection-max-ttl=$CONNECTION_MAX_TTL" &
  CHILD_PID=$!

  if [[ -z "$HEALTH_URL" ]]; then
    transition RUNNING "process started; tunnel health checks disabled"
    if wait "$CHILD_PID"; then child_status=0; else child_status=$?; fi
    CHILD_PID=""
  else
    while true; do
      child_state="$(ps -o stat= -p "$CHILD_PID" 2>/dev/null || true)"
      child_state="${child_state//[[:space:]]/}"
      [[ -n "$child_state" && "$child_state" != Z* ]] || break

      runtime=$((SECONDS - started))
      rotate_client_log
      if check_health; then
        health_ready=1
        health_failures=0
        transition RUNNING "tunnel and OpenAI control-plane healthy"
      elif ((health_ready == 1 || runtime >= STARTUP_GRACE)); then
        health_failures=$((health_failures + 1))
        if ((health_failures == 1)); then
          transition DEGRADED "control-plane check missed; waiting for recovery"
        fi
        if ((health_failures >= HEALTH_FAILURE_LIMIT)); then
          transition RESTARTING "health failed $health_failures times; replacing tunnel client"
          stop_child
          break
        fi
      fi

      sleep "$HEALTH_INTERVAL" &
      MONITOR_PID=$!
      wait "$MONITOR_PID" || true
      MONITOR_PID=""
    done
    if [[ -n "$CHILD_PID" ]]; then
      if wait "$CHILD_PID"; then child_status=0; else child_status=$?; fi
      CHILD_PID=""
    else
      child_status=1
    fi
  fi

  runtime=$((SECONDS - started))
  if ((runtime >= 300)); then backoff=2; fi
  transition RESTARTING "tunnel client exited (code=$child_status); retrying in ${backoff}s"
  sleep "$backoff" &
  MONITOR_PID=$!
  wait "$MONITOR_PID" || true
  MONITOR_PID=""
  backoff=$((backoff * 2))
  ((backoff <= 60)) || backoff=60
done
