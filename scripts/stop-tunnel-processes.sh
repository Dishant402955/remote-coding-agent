#!/usr/bin/env bash
# Stop only processes owned by the dedicated Remote Coding Agent tunnel account.
set -Eeuo pipefail

if (( $# != 0 )); then
  printf 'This helper accepts no arguments.\n' >&2
  exit 2
fi

readonly TUNNEL_USER="remote-coding-agent-tunnel"
if ! /usr/bin/id "$TUNNEL_USER" >/dev/null 2>&1; then
  exit 0
fi

if ! /usr/bin/pgrep -u "$TUNNEL_USER" >/dev/null 2>&1; then
  exit 0
fi

/usr/bin/pkill -TERM -u "$TUNNEL_USER" 2>/dev/null || true
for _ in {1..100}; do
  if ! /usr/bin/pgrep -u "$TUNNEL_USER" >/dev/null 2>&1; then
    exit 0
  fi
  /usr/bin/sleep 0.1
done

/usr/bin/pkill -KILL -u "$TUNNEL_USER" 2>/dev/null || true
/usr/bin/sleep 0.2
if /usr/bin/pgrep -u "$TUNNEL_USER" >/dev/null 2>&1; then
  printf 'Could not stop every process owned by %s.\n' "$TUNNEL_USER" >&2
  exit 1
fi
