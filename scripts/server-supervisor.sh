#!/usr/bin/env bash
# Keep the MCP server healthy and report only meaningful state changes.
set -Eeuo pipefail
umask 077

# The launcher owns fd 9 for its single-instance flock. Supervisors and all
# descendants must never inherit it, or they can keep the lock alive after the
# launcher exits.
exec 9>&-

LOG_FILE="${1:?usage: server-supervisor.sh STATUS_LOG COMMAND [ARG...]}"
shift
(($# > 0)) || { printf 'A server command is required.\n' >&2; exit 2; }
HEALTH_URL="${REMOTE_CODING_AGENT_SUPERVISOR_HEALTH_URL:-}"
HEALTH_INTERVAL="${REMOTE_CODING_AGENT_SUPERVISOR_HEALTH_INTERVAL_SECONDS:-10}"
HEALTH_FAILURE_LIMIT="${REMOTE_CODING_AGENT_SUPERVISOR_HEALTH_FAILURE_LIMIT:-3}"
STARTUP_GRACE="${REMOTE_CODING_AGENT_SUPERVISOR_STARTUP_GRACE_SECONDS:-60}"
CHILD_LOG="${REMOTE_CODING_AGENT_CHILD_LOG_FILE:-/tmp/remote-coding-agent-server-child.log}"
STATE="STOPPED"
CHILD_PID=""
MONITOR_PID=""

rotate_status_log() {
  if [[ -f "$LOG_FILE" ]] && (( $(stat -c '%s' "$LOG_FILE") >= 1048576 )); then
    mv -f -- "$LOG_FILE" "$LOG_FILE.1"
  fi
}

rotate_child_log() {
  [[ -f "$CHILD_LOG" ]] || return 0
  (( $(stat -c '%s' "$CHILD_LOG") >= 10485760 )) || return 0
  cp -f -- "$CHILD_LOG" "$CHILD_LOG.1"
  : > "$CHILD_LOG"
  chmod 600 "$CHILD_LOG" "$CHILD_LOG.1"
}

transition() {
  local next="$1"; shift
  [[ "$STATE" != "$next" ]] || return 0
  rotate_status_log
  local line
  line="$(date '+%Y-%m-%d %H:%M:%S') | STATUS: $next | MCP server | $*"
  printf '%s\n' "$line" >> "$LOG_FILE"
  printf '%s\n' "$line"
  STATE="$next"
}

check_health() {
  curl -fsS --max-time 4 "$HEALTH_URL" >/dev/null 2>&1
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
  transition STOPPED "process tree stopped (exit=$exit_code)"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

backoff=2
transition STARTING "supervisor active"
while true; do
  started=$SECONDS
  health_ready=0
  health_failures=0
  transition STARTING "launching process"
  rotate_child_log
  # Keep the command and tee in one dedicated process group. Avoid process
  # substitution here: its tee can outlive this supervisor, retain inherited
  # descriptors, and keep the launcher lock open after Ctrl+C.
  setsid bash -o pipefail -c '
    log_file="$1"
    shift
    "$@" 2>&1 | tee -a "$log_file"
  ' _ "$CHILD_LOG" "$@" &
  CHILD_PID=$!

  if [[ -z "$HEALTH_URL" ]]; then
    transition RUNNING "process started; HTTP health checks disabled"
    if wait "$CHILD_PID"; then child_status=0; else child_status=$?; fi
  else
    while true; do
      child_state="$(ps -o stat= -p "$CHILD_PID" 2>/dev/null || true)"
      child_state="${child_state//[[:space:]]/}"
      [[ -n "$child_state" && "$child_state" != Z* ]] || break

      runtime=$((SECONDS - started))
      rotate_child_log
      if check_health; then
        health_ready=1
        health_failures=0
        transition RUNNING "readiness endpoint healthy"
      elif ((health_ready == 1 || runtime >= STARTUP_GRACE)); then
        health_failures=$((health_failures + 1))
        if ((health_failures == 1)); then
          transition DEGRADED "readiness check missed; waiting for recovery"
        fi
        if ((health_failures >= HEALTH_FAILURE_LIMIT)); then
          transition RESTARTING "readiness failed $health_failures times; replacing process"
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
  transition RESTARTING "process exited (code=$child_status); retrying in ${backoff}s"
  sleep "$backoff" &
  MONITOR_PID=$!
  wait "$MONITOR_PID" || true
  MONITOR_PID=""
  backoff=$((backoff * 2))
  ((backoff <= 60)) || backoff=60
done
