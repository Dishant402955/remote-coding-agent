# Remote Coding Agent

Remote Coding Agent is a small MCP server that exposes one `run_command` tool. It runs on a machine you control and connects to ChatGPT through **OpenAI Secure MCP Tunnel**.

There are exactly two supported deployment paths:

1. **Google Colab:** run [`notebooks/remote_coding_agent_colab.ipynb`](notebooks/remote_coding_agent_colab.ipynb).
2. **Native Ubuntu:** run [`scripts/setup-ubuntu.sh`](scripts/setup-ubuntu.sh) once, then [`scripts/up.sh`](scripts/up.sh) whenever you want to connect the machine.

The native Ubuntu path includes an upgrade-safe bootstrap at [`bootstrap/setup-remote-coding-agent.sh`](bootstrap/setup-remote-coding-agent.sh). It clones the latest `main` on a new machine, or fast-forwards an existing clean `main` checkout, reruns the installer, and starts the supervised process in `tmux`. It refuses to overwrite local modifications, local-only commits, or an active agent session. The repository does not provide a Docker or Compose deployment.

## Native Ubuntu quick start

Supported platform: Ubuntu 22.04 or newer on x86-64/amd64.

```bash
sudo apt update
sudo apt install -y git curl ca-certificates
git clone https://github.com/Dishant402955/remote-coding-agent.git
cd remote-coding-agent
./scripts/setup-ubuntu.sh
tmux new-session -s remote-agent ./scripts/up.sh
```

`setup-ubuntu.sh` installs `uv`, Python 3.12.16, the locked project dependencies, and the official OpenAI `tunnel-client` v0.0.19 release after verifying its pinned SHA-256. It also creates a locked, non-login system account named `remote-coding-agent-tunnel`, installs the tunnel client under a root-owned directory, and creates a private `workspace/` directory. This one-time setup requires `sudo`.

`up.sh` starts a lightweight, dependency-free dark-mode dashboard at **http://127.0.0.1:3333** before bringing up the MCP server. On first launch, open the dashboard, create a dashboard password, and enter your existing OpenAI Tunnel ID and Runtime API Key there, then click **Start agent**. On later launches, sign in and click **Start agent**; the saved credentials are loaded automatically. Use Settings in the dashboard to rotate them. While the agent is active, **Stop agent** requests a graceful shutdown of the MCP server and tunnel; the launcher then exits and closes the dashboard. Run `./scripts/up.sh` again in your tmux session to start another session. The dashboard shows MCP and tunnel health, live command activity, searchable command history with timestamps, duration, working directory, and exit status, plus the latest 20 agent sessions. It starts the MCP server on `127.0.0.1:8000`, performs an MCP initialization/tool/command smoke test, and runs `tunnel-client` under the dedicated system account. The key is transferred to that account over stdin, not exposed in the normal user's process arguments or environment. Both services have supervisors with bounded exponential backoff, readiness checks, process-group cleanup, and short state-change messages. The console reports `STARTING`, `RUNNING`, `DEGRADED`, `RESTARTING`, `STOPPING`, and `STOPPED`. MCP HTTP requests are logged as `ACCESS START` / `ACCESS END`; `run_command` calls are logged as `TOOL START` / `TOOL END` with a shortened command preview, working directory, exit code, and duration. Common credential forms are redacted; request bodies, authorization headers, MCP session IDs, and command output are not logged. Dashboard command history is stored separately as redacted JSONL in the private local state directory. Server output is mirrored to the attached terminal and the size-bounded server log. Tunnel logs remain readable only with sudo. The MCP idle-session timeout is seven days; tunnel MCP transport connections are refreshed with a 24-hour maximum TTL. `up.sh` also requests a systemd sleep/idle/lid-close inhibitor while the agent runs. Start it inside `tmux` so it survives terminal or SSH disconnects.

After a reboot or whenever you want to reconnect, start a tmux session and run:

```bash
cd ~/projects/remote-coding-agent   # replace this if your clone is elsewhere
tmux new-session -s remote-agent ./scripts/up.sh
```

Detach with `Ctrl+B`, then `D`; reconnect with `tmux attach -t remote-agent`. Stop cleanly by reattaching and pressing `Ctrl+C`. You do not need to reinstall Python or the tunnel client after a reboot. The dashboard password hash and non-secret tunnel ID are stored in `~/.local/state/remote-coding-agent/dashboard.json` with owner-only permissions. The runtime API key is stored separately at `/var/lib/remote-coding-agent-tunnel/credentials.json`, owned by the dedicated tunnel account with mode `600`; it is not readable by the normal MCP command user. A fixed-purpose sudo helper permits credential updates without granting general passwordless sudo. The API key is plaintext at rest inside the tunnel account's private home, so use a trusted machine account and full-disk encryption. Credential changes in Settings take effect on the next agent restart. The sleep inhibitor cannot protect against a power cut, network outage, or manual shutdown. Keep using your existing ChatGPT connector and tunnel.

For a clean machine setup or an in-place update, download and run the standalone bootstrap:

```bash
mkdir -p "$HOME/projects"
curl -fsSL https://raw.githubusercontent.com/Dishant402955/remote-coding-agent/main/bootstrap/setup-remote-coding-agent.sh \
  -o "$HOME/projects/setup-remote-coding-agent.sh"
chmod +x "$HOME/projects/setup-remote-coding-agent.sh"
"$HOME/projects/setup-remote-coding-agent.sh"
```

The bootstrap updates an existing clean checkout at `~/projects/remote-coding-agent` or clones it if absent. It keeps dashboard state and tunnel credentials in their existing system locations. It will not overwrite local modifications or local-only commits, switch branches, or update files while the `remote-agent` tmux session is active. Stop the running agent cleanly before updating. To use a different checkout path, set `REMOTE_CODING_AGENT_PROJECT_DIR` before running the script.

**Migrating from the earlier single-user Ubuntu launcher:** stop its server and tunnel-client processes, revoke the runtime API key used by that version, and create a replacement key. The earlier launcher ran `tunnel-client` under the same Linux account as `run_command`, which allowed a same-UID command to inspect its key through `/proc`. The new setup uses a dedicated system account for the tunnel process.

The dashboard binds only to `127.0.0.1`; it is not exposed to your LAN or the public internet. On a remote machine, access it through SSH port forwarding rather than changing the bind address. The dashboard uses no React, Node.js, database, or external CDN.

## Google Colab

Open [`notebooks/remote_coding_agent_colab.ipynb`](notebooks/remote_coding_agent_colab.ipynb) in Google Colab and run its deployment cell. It installs the project into the temporary Colab runtime, validates the official tunnel-client download, checks the local MCP tool, and starts the Secure MCP Tunnel. Re-enter the tunnel ID and runtime API key after a runtime reset. Run the notebook's cleanup cell when finished.

Colab is ephemeral. Files in its runtime are not a persistent copy of your Ubuntu machine. Colab runs the MCP server and tunnel client in the same runtime identity, so this path does not provide the separate-UID credential protection used by the native Ubuntu launcher. Treat a Colab session as disposable: do not expose valuable credentials or sensitive files to it, and revoke its runtime key when finished if you have any concern it was exposed.

## How it works

```text
ChatGPT
   │ OpenAI Secure MCP Tunnel
   ▼
OpenAI tunnel control plane
   ▲ outbound authenticated connection
   │
tunnel-client (dedicated system account)
   │ http://127.0.0.1:8000/mcp
   ▼
Remote Coding Agent MCP server
   │
   ▼
Shell command running as your Ubuntu user
```

The MCP server binds to loopback and uses `REMOTE_CODING_AGENT_AUTH_MODE=none`. Its `GET /healthz` and `GET /readyz` routes return only liveness/readiness state; the MCP protocol endpoint remains a separate route. The MCP server and `run_command` execute as your normal Ubuntu user; `tunnel-client` runs as the separate locked `remote-coding-agent-tunnel` system account. The runtime API key is sent to that account over stdin and exists in the tunnel process environment only. The launcher invalidates the normal user's cached sudo ticket after the tunnel starts and again during cleanup. Root can still inspect both processes, and the normal user's account can regain privileged access by authenticating with sudo.

## Security: read this before connecting

**This tool executes shell commands as the operating-system user running the server. It is remote code execution, not a read-only file browser.** A command may read, create, modify, or delete any file that this Linux user can access, including files outside `workspace/`.

`workspace/` is the default working directory and the allowed base for the tool's `working_directory` argument. It is **not a filesystem sandbox**: shell commands can still use absolute paths or access other locations allowed by your user account. Do not assume your home directory or personal files are protected. Keep the endpoint on loopback, protect your ChatGPT account and tunnel credentials, and disconnect the tunnel when you are done.

The server strips common secret-bearing environment variables and all `REMOTE_CODING_AGENT_*` runtime settings from child command environments, and the Ubuntu launcher gives the MCP server a minimal environment. The separate tunnel system account prevents ordinary same-user command processes from reading the tunnel client's environment through `/proc`. The launcher also clears the user's cached sudo authorization while the tunnel is active. This is defense in depth, not a full sandbox: root can inspect both processes, and commands still have all filesystem and network access permitted to your normal user. Use a separate Linux account or VM for stronger isolation and keep sensitive credentials out of that account. Do not enter your sudo password in response to an unexpected prompt while the remote tool is active.

Never commit runtime API keys, private work files, or contents of `workspace/`. The Ubuntu launcher does not save the runtime key. If a key is exposed, revoke and replace it.

## Development and verification

Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/). The Ubuntu setup script installs Python 3.12.16 and `uv` for you.

```bash
uv sync --locked --extra dev
uv run ruff check .
uv run pytest
python3 -m json.tool notebooks/remote_coding_agent_colab.ipynb >/dev/null
bash -n scripts/setup-ubuntu.sh scripts/up.sh scripts/server-supervisor.sh scripts/tunnel-client-supervisor.sh scripts/store-tunnel-credentials.sh bootstrap/setup-remote-coding-agent.sh
uv run python scripts/soak-test.py --help
```

GitHub Actions runs the same Python checks, validates notebook JSON, and checks shell script syntax. The workflow deliberately does not build or test a container image because containers are not a supported deployment path.

## Repository layout

```text
src/remote_coding_agent/              MCP server, configuration, auth, command executor
tests/                                Unit and HTTP/MCP integration tests
notebooks/remote_coding_agent_colab.ipynb
scripts/setup-ubuntu.sh                One-time native Ubuntu setup
scripts/up.sh                          Start supervised MCP server + Secure MCP Tunnel
scripts/server-supervisor.sh           Restart and health-watch the MCP server
scripts/tunnel-client-supervisor.sh    Restart and health-watch Secure MCP Tunnel
scripts/soak-test.py                    Seven-day-default MCP reliability test
bootstrap/setup-remote-coding-agent.sh One-shot clean-machine bootstrap
RELIABILITY_REVIEW.md                   Adversarial review and residual limits
README.md                              Overview, quick starts, security model
DEPLOYMENT.md                          Detailed deployment and recovery guide
pyproject.toml / uv.lock               Project metadata and pinned dependencies
```
