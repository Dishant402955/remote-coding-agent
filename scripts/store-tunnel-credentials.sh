#!/usr/bin/env bash
# Read or atomically store tunnel credentials as the dedicated tunnel account.
set -Eeuo pipefail
umask 077

TUNNEL_HOME="/var/lib/remote-coding-agent-tunnel"
TARGET="$TUNNEL_HOME/credentials.json"
MODE="${1:-}"

case "$MODE" in
  read)
    python3 - "$TARGET" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        value = json.load(stream)
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit("tunnel credentials are unavailable or invalid") from exc
if not isinstance(value, dict):
    raise SystemExit("tunnel credentials must be an object")
tunnel_id = value.get("tunnel_id")
api_key = value.get("api_key")
if not isinstance(tunnel_id, str) or not tunnel_id.strip():
    raise SystemExit("tunnel_id is missing")
if not isinstance(api_key, str) or len(api_key.strip()) < 16:
    raise SystemExit("api_key is missing or invalid")
print(json.dumps({"tunnel_id": tunnel_id, "api_key": api_key}, separators=(",", ":")))
PY
    ;;
  write)
    python3 - "$TARGET" <<'PY'
import json
import os
import sys
import tempfile

target = sys.argv[1]
raw = sys.stdin.read(8193)
if len(raw) > 8192:
    raise SystemExit("credential payload too large")
try:
    incoming = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit("credential payload must be valid JSON") from exc
if not isinstance(incoming, dict):
    raise SystemExit("credential payload must be an object")
tunnel_id = incoming.get("tunnel_id")
api_key = incoming.get("api_key")
if not isinstance(tunnel_id, str) or not tunnel_id.strip():
    raise SystemExit("tunnel_id is required")
if not isinstance(api_key, str):
    raise SystemExit("api_key must be a string")
if not api_key.strip() and os.path.exists(target):
    try:
        with open(target, encoding="utf-8") as current:
            api_key = json.load(current).get("api_key", "")
    except (OSError, json.JSONDecodeError, AttributeError):
        raise SystemExit("existing credentials are invalid")
if not isinstance(api_key, str) or len(api_key.strip()) < 16:
    raise SystemExit("api_key is required")

directory = os.path.dirname(target)
os.makedirs(directory, mode=0o700, exist_ok=True)
fd, temp_path = tempfile.mkstemp(prefix=".credentials.", dir=directory)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(
            {"tunnel_id": tunnel_id.strip(), "api_key": api_key.strip()},
            handle,
            separators=(",", ":"),
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, target)
    os.chmod(target, 0o600)
finally:
    if os.path.exists(temp_path):
        os.unlink(temp_path)
PY
    ;;
  *)
    printf 'Usage: %s {read|write}\n' "$0" >&2
    exit 2
    ;;
esac
