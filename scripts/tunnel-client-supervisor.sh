#!/usr/bin/env bash
# Keep Secure MCP Tunnel alive; the runtime key arrives over stdin, never argv.
set -Eeuo pipefail
umask 077
TUNNEL_CLIENT="${1:?usage: tunnel-client-supervisor.sh CLIENT MCP_URL LOG TTL HEALTH_URL}"
MCP_SERVER_URL="${2:?MCP URL is required}"
LOG_FILE="${3:?supervisor log file is required}"
CONNECTION_MAX_TTL="${4:-24h}"
HEALTH_URL="${5:-}"
IFS= read -r CONTROL_PLANE_API_KEY || { printf 'Missing runtime API key on stdin.\n' >&2; exit 2; }
[[ -n "$CONTROL_PLANE_API_KEY" ]] || { printf 'Runtime API key cannot be empty.\n' >&2; exit 2; }
export CONTROL_PLANE_API_KEY MCP_SERVER_URL
HEALTH_INTERVAL="${REMOTE_CODING_AGENT_TUNNEL_HEALTH_INTERVAL_SECONDS:-30}"
HEALTH_FAILURE_LIMIT="${REMOTE_CODING_AGENT_TUNNEL_HEALTH_FAILURE_LIMIT:-3}"
STARTUP_GRACE_SECONDS="${REMOTE_CODING_AGENT_TUNNEL_STARTUP_GRACE_SECONDS:-180}"
log() {
  if [[ -f "$LOG_FILE" ]] && (( $(stat -c '%s' "$LOG_FILE") > 1048576 )); then
    mv -f -- "$LOG_FILE" "$LOG_FILE.1"
  fi
  printf '%s %s\n' "$(date -Is)" "$*" >> "$LOG_FILE"
}
terminate_child() {
  [[ -n "$CHILD_PID" ]] || return 0
  kill -TERM -- "-$CHILD_PID" 2>/dev/null || true
  for _ in {1..50}; do
    state="$(ps -o stat= -p "$CHILD_PID" 2>/dev/null || true)"
    state="${state//[[:space:]]/}"
    [[ -n "$state" && "$state" != Z* ]] || break
    sleep 0.1
  done
  kill -KILL -- "-$CHILD_PID" 2>/dev/null || true
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
CHILD_PID=""
stop_child() {
  [[ -n "$CHILD_PID" ]] || return 0
  if kill -0 "$CHILD_PID" 2>/dev/null; then
    kill -TERM -- "-$CHILD_PID" 2>/dev/null || true
    for _ in {1..50}; do
      kill -0 "$CHILD_PID" 2>/dev/null || break
      sleep 0.1
    done
    kill -KILL -- "-$CHILD_PID" 2>/dev/null || true
    wait "$CHILD_PID" 2>/dev/null || true
  fi
  CHILD_PID=""
}
trap stop_child EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
log "tunnel supervisor started; max connection TTL=$CONNECTION_MAX_TTL"
"$TUNNEL_CLIENT" doctor --explain || { log "tunnel-client doctor failed"; exit 1; }
backoff=2
while true; do
  started=$SECONDS
  log "starting tunnel-client"
  setsid "$TUNNEL_CLIENT" run "--mcp.connection-max-ttl=$CONNECTION_MAX_TTL" &
  CHILD_PID=$!
  failures=0
  while true; do
    state="$(ps -o stat= -p "$CHILD_PID" 2>/dev/null || true)"
    state="${state//[[:space:]]/}"
    [[ -n "$state" && "$state" != Z* ]] || break
    runtime=$((SECONDS - started))
    if [[ -n "$HEALTH_URL" ]] && ((runtime >= STARTUP_GRACE_SECONDS)); then
      if check_health; then failures=0; else
        failures=$((failures + 1))
        log "tunnel health check failed count=$failures"
      fi
      if ((failures >= HEALTH_FAILURE_LIMIT)); then
        log "tunnel unhealthy; terminating child process group"
        terminate_child
        break
      fi
    fi
    sleep "$HEALTH_INTERVAL" &
    monitor_pid=$!
    wait "$monitor_pid" || true
  done
  if wait "$CHILD_PID"; then child_status=0; else child_status=$?; fi
  CHILD_PID=""
  runtime=$((SECONDS - started))
  log "tunnel-client exited status=$child_status runtime_seconds=$runtime"
  if ((runtime >= 300)); then backoff=2; fi
  log "restarting tunnel-client in ${backoff}s"
  sleep "$backoff"
  backoff=$((backoff * 2))
  ((backoff <= 60)) || backoff=60
done
