# Remote Coding Agent

Remote Coding Agent is a small MCP server that exposes one `run_command` tool. It runs on a machine you control and connects to ChatGPT through **OpenAI Secure MCP Tunnel**.

There are exactly two supported deployment paths:

1. **Google Colab:** run [`notebooks/remote_coding_agent_colab.ipynb`](notebooks/remote_coding_agent_colab.ipynb).
2. **Native Ubuntu:** run [`scripts/setup-ubuntu.sh`](scripts/setup-ubuntu.sh) once, then [`scripts/up.sh`](scripts/up.sh) whenever you want to connect the machine.

The repository contains the application source, tests, notebook, Ubuntu scripts, and documentation. It does not provide a Docker or Compose deployment.

## Native Ubuntu quick start

Supported platform: Ubuntu 22.04 or newer on x86-64/amd64.

```bash
sudo apt update
sudo apt install -y git curl ca-certificates
git clone https://github.com/Dishant402955/remote-coding-agent.git
cd remote-coding-agent
./scripts/setup-ubuntu.sh
./scripts/up.sh
```

`setup-ubuntu.sh` installs `uv`, Python 3.12.16, the locked project dependencies, and the official OpenAI `tunnel-client` v0.0.19 release after verifying its pinned SHA-256. It also creates a locked, non-login system account named `remote-coding-agent-tunnel`, installs the tunnel client under a root-owned directory, and creates a private `workspace/` directory. This one-time setup requires `sudo`.

`up.sh` prompts for the existing OpenAI tunnel ID and runtime API key every time. The key is hidden while typing and is not saved to disk. It starts the MCP server on `127.0.0.1:8000` as your normal Ubuntu user, performs a real MCP initialization/tool/command smoke test, and runs `tunnel-client` under the dedicated system account. The key is transferred to that account over stdin, not exposed in the normal user's process arguments or environment. The script runs tunnel diagnostics and waits for `/healthz`, `/readyz`, and a successful OpenAI control-plane poll. Keep that terminal open. Press `Ctrl+C` to stop both processes; stopping the isolated tunnel account may ask for your sudo password again.

After a reboot or whenever you want to reconnect, only run:

```bash
cd ~/projects/remote-coding-agent   # replace this if your clone is elsewhere
./scripts/up.sh
```

You do not need to reinstall Python or the tunnel client each time. You do need to authorize sudo and enter the tunnel ID and runtime API key at each start. Keep using your existing ChatGPT connector and tunnel; do not create a new one just for a reboot.

**Migrating from the earlier single-user Ubuntu launcher:** stop its server and tunnel-client processes, revoke the runtime API key used by that version, and create a replacement key. The earlier launcher ran `tunnel-client` under the same Linux account as `run_command`, which allowed a same-UID command to inspect its key through `/proc`. The new setup uses a dedicated system account for the tunnel process.

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

The MCP server binds to loopback and uses `REMOTE_CODING_AGENT_AUTH_MODE=none`. The local endpoint is not exposed directly to the LAN or public internet. The MCP server and `run_command` execute as your normal Ubuntu user; `tunnel-client` runs as the separate locked `remote-coding-agent-tunnel` system account. The runtime API key is sent to that account over stdin and exists in the tunnel process environment only. The launcher invalidates the normal user's cached sudo ticket before reporting the tunnel ready. Root can still inspect both processes, and the normal user's account can regain privileged access by authenticating with sudo.

## Security: read this before connecting

**This tool executes shell commands as the operating-system user running the server. It is remote code execution, not a read-only file browser.** A command may read, create, modify, or delete any file that this Linux user can access, including files outside `workspace/`.

`workspace/` is the default working directory and the allowed base for the tool's `working_directory` argument. It is **not a filesystem sandbox**: shell commands can still use absolute paths or access other locations allowed by your user account. Do not assume your home directory or personal files are protected. Keep the endpoint on loopback, protect your ChatGPT account and tunnel credentials, and disconnect the tunnel when you are done.

The server strips common secret-bearing environment variables from child command environments, and the Ubuntu launcher gives the MCP server a minimal environment. The separate tunnel system account prevents ordinary same-user command processes from reading the tunnel client's environment through `/proc`. The launcher also clears the user's cached sudo authorization while the tunnel is active. This is defense in depth, not a full sandbox: root can inspect both processes, and commands still have all filesystem and network access permitted to your normal user. Use a separate Linux account or VM for stronger isolation and keep sensitive credentials out of that account. Do not enter your sudo password in response to an unexpected prompt while the remote tool is active.

Never commit runtime API keys, private work files, or contents of `workspace/`. The Ubuntu launcher does not save the runtime key. If a key is exposed, revoke and replace it.

## Development and verification

Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/). The Ubuntu setup script installs Python 3.12.16 and `uv` for you.

```bash
uv sync --locked --extra dev
uv run ruff check .
uv run pytest
python -m json.tool notebooks/remote_coding_agent_colab.ipynb >/dev/null
bash -n scripts/setup-ubuntu.sh scripts/up.sh
```

GitHub Actions runs the same Python checks, validates notebook JSON, and checks shell script syntax. The workflow deliberately does not build or test a container image because containers are not a supported deployment path.

## Repository layout

```text
src/remote_coding_agent/              MCP server, configuration, auth, command executor
tests/                                Unit and HTTP/MCP integration tests
notebooks/remote_coding_agent_colab.ipynb
scripts/setup-ubuntu.sh                One-time native Ubuntu setup
scripts/up.sh                          Start MCP server + Secure MCP Tunnel
README.md                              Overview, quick starts, security model
DEPLOYMENT.md                          Detailed deployment and recovery guide
pyproject.toml / uv.lock               Project metadata and pinned dependencies
```
