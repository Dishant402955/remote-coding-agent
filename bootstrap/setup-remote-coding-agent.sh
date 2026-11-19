#!/usr/bin/env bash
# Idempotently install/update Remote Coding Agent on native Ubuntu; leave launch to scripts/up.sh.
set -Eeuo pipefail
umask 077

REPOSITORY_URL="https://github.com/Dishant402955/remote-coding-agent.git"
PROJECT_PARENT="${REMOTE_CODING_AGENT_PROJECT_PARENT:-$HOME/projects}"
PROJECT_DIR="${REMOTE_CODING_AGENT_PROJECT_DIR:-$PROJECT_PARENT/remote-coding-agent}"
BRANCH="main"
SESSION_NAME="${REMOTE_CODING_AGENT_TMUX_SESSION:-remote-agent}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$EUID" -ne 0 ]] || die "Run this script as your normal Ubuntu user, not with sudo."
[[ -f /etc/os-release ]] || die "Cannot identify this operating system."
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "Ubuntu is required; detected ${ID:-unknown}."
if [[ "${VERSION_ID:-0}" != "22.04" ]] && ! dpkg --compare-versions "${VERSION_ID:-0}" ge "22.04"; then
  die "Ubuntu 22.04 or newer is required; detected ${VERSION_ID:-unknown}."
fi
case "$(uname -m)" in
  x86_64|amd64) ;;
  *) die "This setup currently supports x86-64/amd64 Ubuntu only." ;;
esac

for tool in git bash sudo; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required. Install it before continuing."
done
[[ -t 0 && -t 1 ]] || die "Run this script from an interactive terminal."

# Updating files while the old launcher is active can leave a mixed-version process.
if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  die "tmux session '$SESSION_NAME' is active. Stop it cleanly first, then rerun this setup."
fi

log "Preparing project directory: $PROJECT_DIR"
mkdir -p "$PROJECT_PARENT"

if [[ -e "$PROJECT_DIR" ]]; then
  [[ -d "$PROJECT_DIR/.git" ]] || die "$PROJECT_DIR exists but is not a Git checkout. Move it aside; this script will not overwrite it."
  actual_remote="$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || true)"
  case "$actual_remote" in
    "$REPOSITORY_URL"|"git@github.com:Dishant402955/remote-coding-agent.git") ;;
    *) die "The existing checkout has an unexpected origin ($actual_remote). Refusing to update it." ;;
  esac

  actual_branch="$(git -C "$PROJECT_DIR" branch --show-current)"
  [[ "$actual_branch" == "$BRANCH" ]] || die "Existing checkout is on '$actual_branch', not '$BRANCH'. Switch it to main manually; this script will not create or switch branches."

  if [[ -n "$(git -C "$PROJECT_DIR" status --porcelain)" ]]; then
    die "The existing checkout has local changes. Commit or stash them before updating; nothing was overwritten."
  fi

  log "Fetching the latest origin/$BRANCH"
  git -C "$PROJECT_DIR" fetch --prune origin "$BRANCH"
  counts="$(git -C "$PROJECT_DIR" rev-list --left-right --count HEAD...origin/$BRANCH)"
  ahead="${counts%%[[:space:]]*}"
  behind="${counts##*[[:space:]]}"
  [[ "$ahead" == "0" ]] || die "The local main branch has $ahead commit(s) not on origin/main. Push or back them up before updating."
  if [[ "$behind" != "0" ]]; then
    log "Fast-forwarding the existing checkout by $behind commit(s)"
    git -C "$PROJECT_DIR" merge --ff-only "origin/$BRANCH"
  else
    log "The existing checkout is already current."
  fi
else
  log "Cloning the latest $BRANCH branch"
  git clone --branch "$BRANCH" --single-branch "$REPOSITORY_URL" "$PROJECT_DIR"
fi

actual_remote="$(git -C "$PROJECT_DIR" remote get-url origin)"
actual_branch="$(git -C "$PROJECT_DIR" branch --show-current)"
[[ "$actual_branch" == "$BRANCH" ]] || die "Expected branch '$BRANCH', got '$actual_branch'."
case "$actual_remote" in
  "$REPOSITORY_URL"|"git@github.com:Dishant402955/remote-coding-agent.git") ;;
  *) die "Repository origin changed unexpectedly: $actual_remote" ;;
esac

for relative_path in \
  scripts/setup-ubuntu.sh \
  scripts/up.sh \
  scripts/server-supervisor.sh \
  scripts/tunnel-client-supervisor.sh \
  scripts/stop-tunnel-processes.sh; do
  [[ -f "$PROJECT_DIR/$relative_path" ]] || die "Updated repository is missing $relative_path."
done

log "Installing/updating Python, dependencies, tunnel-client, and the isolated tunnel account"
bash "$PROJECT_DIR/scripts/setup-ubuntu.sh"

command -v tmux >/dev/null 2>&1 || die "tmux is missing after setup-ubuntu.sh. Install it with: sudo apt-get install tmux"
[[ -x "$PROJECT_DIR/scripts/up.sh" ]] || die "scripts/up.sh is not executable after setup."

log "Installation completed successfully"
printf 'Repository: %s\n' "$PROJECT_DIR"
printf 'Branch: %s\n' "$(git -C "$PROJECT_DIR" branch --show-current)"
printf 'Commit: %s\n' "$(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
printf 'Credential input: terminal prompt at agent startup (not saved)\n'
printf '\nNEXT STEP: start the agent manually from the repository directory:\n'
printf '  cd %q\n' "$PROJECT_DIR"
printf '  ./scripts/up.sh\n\n'
printf 'The launcher will ask for your Tunnel ID and Runtime API Key in this terminal.\n'
printf 'The dashboard at http://127.0.0.1:3333 displays the live log stream only.\n'
printf 'This setup script does not start the agent.\n'
