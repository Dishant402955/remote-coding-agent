#!/usr/bin/env bash
# Install the native Ubuntu runtime and the official OpenAI tunnel client.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="$HOME/.local/bin"
SYSTEM_BASE="/usr/local/lib/remote-coding-agent"
TUNNEL_INSTALL_DIR="$SYSTEM_BASE/tunnel-client"
TUNNEL_USER="remote-coding-agent-tunnel"
TUNNEL_HOME="/var/lib/$TUNNEL_USER"
SYSTEM_STAGING_DIR="$SYSTEM_BASE/.tunnel-client.new.$$"
SYSTEM_BACKUP_DIR="$SYSTEM_BASE/.tunnel-client.previous.$$"
TUNNEL_CLIENT_VERSION="v0.0.19"
TUNNEL_CLIENT_SHA256="9ee9448592345a9dd1517fb79791ab54d24f051142eb92bcbe8cd355effc9142"
TEMP_DIR=""
STAGING_DIR=""
BACKUP_DIR=""

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
  if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
    rm -rf -- "$STAGING_DIR"
  fi
  if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
    rm -rf -- "$BACKUP_DIR"
  fi
  if [[ -d "$SYSTEM_STAGING_DIR" ]]; then
    sudo -n rm -rf -- "$SYSTEM_STAGING_DIR" 2>/dev/null || true
  fi
  if [[ -d "$SYSTEM_BACKUP_DIR" ]]; then
    if [[ ! -e "$TUNNEL_INSTALL_DIR" ]]; then
      sudo -n mv -- "$SYSTEM_BACKUP_DIR" "$TUNNEL_INSTALL_DIR" 2>/dev/null || true
    else
      sudo -n rm -rf -- "$SYSTEM_BACKUP_DIR" 2>/dev/null || true
    fi
  fi
  # Do not leave a reusable sudo timestamp for the MCP runtime after setup.
  sudo -K 2>/dev/null || true
}
trap cleanup EXIT

[[ -f "$ROOT/pyproject.toml" ]] || die "Run this script from a cloned remote-coding-agent repository."
[[ -f /etc/os-release ]] || die "Cannot identify this operating system."
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "This installer supports Ubuntu; detected ${ID:-unknown}."
if [[ "${VERSION_ID:-0}" != "22.04" ]] && ! dpkg --compare-versions "${VERSION_ID:-0}" ge "22.04"; then
  die "Ubuntu 22.04 or newer is required; detected ${VERSION_ID:-unknown}."
fi

case "$(uname -m)" in
  x86_64|amd64) TUNNEL_ARCH="amd64" ;;
  *) die "The official tunnel-client installer currently supports x86_64/amd64; detected $(uname -m)." ;;
esac
command -v sudo >/dev/null 2>&1 || die "sudo is required to install the dedicated tunnel-client system account."
sudo -v

log "Checking Ubuntu prerequisites"
MISSING_PACKAGES=()
for item in git curl unzip sha256sum python3 setsid pgrep pkill; do
  if ! command -v "$item" >/dev/null 2>&1; then
    case "$item" in
      sha256sum) MISSING_PACKAGES+=(coreutils) ;;
      setsid) MISSING_PACKAGES+=(util-linux) ;;
      pgrep|pkill) MISSING_PACKAGES+=(procps) ;;
      *) MISSING_PACKAGES+=("$item") ;;
    esac
  fi
done
if ! dpkg-query -W -f='${Status}' ca-certificates 2>/dev/null | grep -q 'install ok installed'; then
  MISSING_PACKAGES+=(ca-certificates)
fi
if ((${#MISSING_PACKAGES[@]})); then
  command -v sudo >/dev/null 2>&1 || die "Missing prerequisites (${MISSING_PACKAGES[*]}) and sudo is unavailable."
  sudo apt-get update
  mapfile -t MISSING_PACKAGES < <(printf '%s\n' "${MISSING_PACKAGES[@]}" | sort -u)
  sudo apt-get install -y ca-certificates "${MISSING_PACKAGES[@]}"
fi

log "Installing or locating uv"
export PATH="$INSTALL_ROOT:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$INSTALL_ROOT:$PATH"
fi
command -v uv >/dev/null 2>&1 || die "uv installation did not produce an executable on PATH."

log "Preparing a dedicated system account for tunnel-client"
if account_entry="$(getent passwd "$TUNNEL_USER")"; then
  IFS=: read -r _ _ account_uid _ _ account_home account_shell <<< "$account_entry"
  [[ "$account_home" == "$TUNNEL_HOME" && "$account_shell" == "/usr/sbin/nologin" ]] || die "Existing $TUNNEL_USER account does not match the expected locked system account; inspect it manually."
  ((account_uid < 1000)) || die "$TUNNEL_USER exists but is not a system UID; refusing to reuse it."
else
  sudo useradd --system --user-group --create-home --home-dir "$TUNNEL_HOME" --shell /usr/sbin/nologin "$TUNNEL_USER"
fi
sudo install -d -o "$TUNNEL_USER" -g "$TUNNEL_USER" -m 700 "$TUNNEL_HOME"

log "Installing Python 3.12.16 and synchronizing the locked project environment"
cd "$ROOT"
uv python install 3.12.16
uv sync --locked --python 3.12.16 --extra dev
mkdir -p "$ROOT/workspace"
chmod 700 "$ROOT/workspace"

log "Resolving the pinned official OpenAI tunnel-client release"
TEMP_DIR="$(mktemp -d)"
RELEASE_JSON="$TEMP_DIR/release.json"
ZIP_PATH="$TEMP_DIR/tunnel-client.zip"
curl -fsSL \
  -H 'Accept: application/vnd.github+json' \
  -H 'User-Agent: remote-coding-agent-ubuntu-installer' \
  "https://api.github.com/repos/openai/tunnel-client/releases/tags/${TUNNEL_CLIENT_VERSION}" \
  -o "$RELEASE_JSON"

IFS=$'\t' read -r RELEASE_TAG ASSET_NAME DOWNLOAD_URL EXPECTED_DIGEST < <(
  python3 - "$RELEASE_JSON" "$TUNNEL_ARCH" "$TUNNEL_CLIENT_VERSION" "$TUNNEL_CLIENT_SHA256" <<'PY'
import json
import sys

release_path, arch, pinned_version, pinned_digest = sys.argv[1:]
with open(release_path, encoding="utf-8") as stream:
    release = json.load(stream)
tag = release.get("tag_name", "")
if tag != pinned_version:
    raise SystemExit("GitHub release tag does not match the pinned tunnel-client version")
name = f"tunnel-client-{tag}-linux-{arch}.zip"
asset = next((item for item in release.get("assets", []) if item.get("name") == name), None)
if asset is None:
    raise SystemExit(f"Release {tag} does not publish the expected asset {name}")
digest = asset.get("digest", "")
if digest != f"sha256:{pinned_digest}":
    raise SystemExit("The published release digest differs from the pinned SHA-256; refusing installation")
url = asset.get("browser_download_url", "")
if not url.startswith("https://github.com/openai/tunnel-client/releases/download/"):
    raise SystemExit("The release asset URL is not an official OpenAI tunnel-client release URL")
print(tag, name, url, digest, sep="\t")
PY
)
[[ -n "$RELEASE_TAG" && -n "$ASSET_NAME" && -n "$DOWNLOAD_URL" && -n "$EXPECTED_DIGEST" ]] || die "Could not resolve the official tunnel-client asset metadata."

log "Downloading $ASSET_NAME and verifying its published SHA-256"
curl -fL "$DOWNLOAD_URL" -o "$ZIP_PATH"
[[ "${EXPECTED_DIGEST#sha256:}" == "$TUNNEL_CLIENT_SHA256" ]] || die "Tunnel-client digest metadata did not match the pinned checksum."
printf '%s  %s\n' "$TUNNEL_CLIENT_SHA256" "$ZIP_PATH" | sha256sum -c -

python3 - "$ZIP_PATH" <<'PY'
from pathlib import PurePosixPath
from zipfile import ZipFile
import sys

required = {
    "tunnel-client",
    "cloudflared",
    "cloudflared-manifest.json",
    "LICENSE",
    "NOTICE",
    "tunnel-client-v0.0.19-linux-amd64-licenses.txt",
    "tunnel-client-v0.0.19-linux-amd64.spdx.json",
}
with ZipFile(sys.argv[1]) as archive:
    names = set()
    for item in archive.infolist():
        path = PurePosixPath(item.filename)
        if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
            raise SystemExit(f"Unsafe or unexpected path in tunnel-client archive: {item.filename!r}")
        names.add(item.filename)
    missing = required - names
    if missing:
        raise SystemExit(f"Tunnel-client archive is missing required files: {sorted(missing)}")
PY

mkdir -p "$INSTALL_ROOT"
STAGING_DIR="$INSTALL_ROOT/.tunnel-client.new.$$"
BACKUP_DIR="$INSTALL_ROOT/.tunnel-client.previous.$$"
rm -rf -- "$STAGING_DIR" "$BACKUP_DIR"
mkdir -m 700 "$STAGING_DIR"
unzip -q "$ZIP_PATH" -d "$STAGING_DIR"
chmod 755 "$STAGING_DIR/tunnel-client" "$STAGING_DIR/cloudflared"
"$STAGING_DIR/tunnel-client" --version >/dev/null

sudo install -d -o root -g root -m 755 "$SYSTEM_BASE"
sudo rm -rf -- "$SYSTEM_STAGING_DIR" "$SYSTEM_BACKUP_DIR"
sudo install -d -o root -g root -m 755 "$SYSTEM_STAGING_DIR"
sudo install -o root -g root -m 755 "$STAGING_DIR/tunnel-client" "$SYSTEM_STAGING_DIR/tunnel-client"
sudo install -o root -g root -m 755 "$STAGING_DIR/cloudflared" "$SYSTEM_STAGING_DIR/cloudflared"
sudo install -o root -g root -m 644 "$STAGING_DIR/cloudflared-manifest.json" "$SYSTEM_STAGING_DIR/cloudflared-manifest.json"
sudo install -o root -g root -m 644 "$STAGING_DIR/LICENSE" "$SYSTEM_STAGING_DIR/LICENSE"
sudo install -o root -g root -m 644 "$STAGING_DIR/NOTICE" "$SYSTEM_STAGING_DIR/NOTICE"
sudo install -o root -g root -m 644 "$STAGING_DIR/tunnel-client-v0.0.19-linux-amd64-licenses.txt" "$SYSTEM_STAGING_DIR/tunnel-client-v0.0.19-linux-amd64-licenses.txt"
sudo install -o root -g root -m 644 "$STAGING_DIR/tunnel-client-v0.0.19-linux-amd64.spdx.json" "$SYSTEM_STAGING_DIR/tunnel-client-v0.0.19-linux-amd64.spdx.json"
sudo -u "$TUNNEL_USER" -- "$SYSTEM_STAGING_DIR/tunnel-client" --version >/dev/null
if [[ -d "$TUNNEL_INSTALL_DIR" ]]; then
  sudo mv -- "$TUNNEL_INSTALL_DIR" "$SYSTEM_BACKUP_DIR"
fi
if sudo mv -- "$SYSTEM_STAGING_DIR" "$TUNNEL_INSTALL_DIR"; then
  sudo rm -rf -- "$SYSTEM_BACKUP_DIR"
else
  [[ ! -d "$SYSTEM_BACKUP_DIR" ]] || sudo mv -- "$SYSTEM_BACKUP_DIR" "$TUNNEL_INSTALL_DIR"
  die "Could not install tunnel-client into $TUNNEL_INSTALL_DIR."
fi

# Remove the first user-local install created by the earlier single-user setup.
if [[ -d "$HOME/.local/bin/tunnel-client" && ! -L "$HOME/.local/bin/tunnel-client" ]]; then
  rm -rf -- "$HOME/.local/bin/tunnel-client"
fi

log "Installation complete"
printf 'Repository: %s\n' "$ROOT"
printf 'Python: %s\n' "$("$ROOT/.venv/bin/python" --version)"
printf 'uv: %s\n' "$(uv --version)"
printf 'Tunnel client: %s\n' "$(sudo -u "$TUNNEL_USER" -- "$TUNNEL_INSTALL_DIR/tunnel-client" --version)"
printf 'Tunnel service account: %s\n' "$TUNNEL_USER"
printf 'Workspace: %s\n' "$ROOT/workspace"
printf '\nNext: run %s/scripts/up.sh\n' "$ROOT"
