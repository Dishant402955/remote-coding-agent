#!/usr/bin/env bash
# Clone, install, and start the supervised Remote Coding Agent in tmux.
set -Eeuo pipefail
umask 077

REPOSITORY_URL="https://github.com/Dishant402955/remote-coding-agent.git"
PROJECT_PARENT="$HOME/projects"
PROJECT_DIR="$PROJECT_PARENT/remote-coding-agent"
BRANCH="main"
SESSION_NAME="remote-agent"

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

for tool in git bash sudo systemd-inhibit; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required. Install it before continuing."
done
[[ -t 0 && -t 1 ]] || die "Run this script from an interactive terminal."
[[ ! -e "$PROJECT_DIR" ]] || die "$PROJECT_DIR already exists. Stop the old agent and remove or move that directory before running this setup."

if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  die "A tmux session named '$SESSION_NAME' already exists. Inspect or stop it before reinstalling."
fi

log "Preparing $PROJECT_PARENT"
mkdir -p "$PROJECT_PARENT"

log "Cloning the latest $BRANCH branch"
git clone --branch "$BRANCH" --single-branch "$REPOSITORY_URL" "$PROJECT_DIR"
actual_remote="$(git -C "$PROJECT_DIR" remote get-url origin)"
[[ "$actual_remote" == "$REPOSITORY_URL" ]] || die "The cloned repository remote did not match the expected GitHub repository."
actual_branch="$(git -C "$PROJECT_DIR" branch --show-current)"
[[ "$actual_branch" == "$BRANCH" ]] || die "Expected branch '$BRANCH', got '$actual_branch'."

for relative_path in \
  scripts/setup-ubuntu.sh \
  scripts/up.sh \
  scripts/server-supervisor.sh \
  scripts/tunnel-client-supervisor.sh; do
  [[ -x "$PROJECT_DIR/$relative_path" ]] || die "Repository clone is missing executable $relative_path."
done

log "Installing Python, tunnel-client, tmux, and the isolated tunnel account"
bash "$PROJECT_DIR/scripts/setup-ubuntu.sh"
command -v tmux >/dev/null 2>&1 || die "tmux was not installed successfully."

log "Starting the agent in tmux"
printf 'The next prompt asks for your existing tunnel ID and runtime API key.\n'
printf 'The key is hidden while typing and is not saved to disk.\n'
printf 'The agent blocks automatic sleep while it is running. Keep this machine powered and online.\n'
printf 'Detach with Ctrl+B, then D. Reattach with: tmux attach -t %s\n' "$SESSION_NAME"
printf 'Stop cleanly by reattaching and pressing Ctrl+C.\n\n'
printf -v launch_command 'exec %q' "$PROJECT_DIR/scripts/up.sh"
exec tmux new-session -s "$SESSION_NAME" -c "$PROJECT_DIR" "$launch_command"
