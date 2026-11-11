#!/usr/bin/env bash
# Restart the MCP server on exit and after repeated failed local health checks.
set -Eeuo pipefail
umask 077
LOG_FILE="${1:?usage: server-supervisor.sh LOG_FILE COMMAND [ARG...]}"
shift
(($# > 0)) || { printf 'A server command is required.\n' >&2; exit 2; }
HEALTH_URL="${REMOTE_CODING_AGENT_SUPERVISOR_HEALTH_URL:-}"
HEALTH_INTERVAL="${REMOTE_CODING_AGENT_SUPERVISOR_HEALTH_INTERVAL_SECONDS:-10}"
HEALTH_FAILURE_LIMIT="${REMOTE_CODING_AGENT_SUPERVISOR_HEALTH_FAILURE_LIMIT:-3}"
log() {
  if [[ -f "$LOG_FILE" ]] && (( $(stat -c '%s' "$LOG_FILE") > 1048576 )); then
    mv -f -- "$LOG_FILE" "$LOG_FILE.1"
  fi
  printf '%s %s\n' "$(date -Is)" "$*" >> "$LOG_FILE"
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
  local status
  status="$(curl -sS --max-time 4 -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || true)"
  [[ "$status" =~ ^[234][0-9][0-9]$ ]]
}
backoff=2
log "server supervisor started health_url=${HEALTH_URL:-disabled}"
while true; do
  started=$SECONDS
  setsid "$@" &
  CHILD_PID=$!
  log "started server child pid=$CHILD_PID"
  if [[ -z "$HEALTH_URL" ]]; then
    if wait "$CHILD_PID"; then child_status=0; else child_status=$?; fi
  else
    failures=0
    while true; do
      state="$(ps -o stat= -p "$CHILD_PID" 2>/dev/null || true)"
      state="${state//[[:space:]]/}"
      [[ -n "$state" && "$state" != Z* ]] || break
      if check_health; then failures=0; else
        failures=$((failures + 1))
        log "server health check failed count=$failures"
      fi
      if ((failures >= HEALTH_FAILURE_LIMIT)); then
        log "server unhealthy; terminating child process group"
        terminate_child
        break
      fi
      sleep "$HEALTH_INTERVAL" &
      monitor_pid=$!
      wait "$monitor_pid" || true
    done
    if wait "$CHILD_PID"; then child_status=0; else child_status=$?; fi
  fi
  CHILD_PID=""
  runtime=$((SECONDS - started))
  log "server child exited status=$child_status runtime_seconds=$runtime"
  if ((runtime >= 300)); then backoff=2; fi
  log "restarting server in ${backoff}s"
  sleep "$backoff"
  backoff=$((backoff * 2))
  ((backoff <= 60)) || backoff=60
done
