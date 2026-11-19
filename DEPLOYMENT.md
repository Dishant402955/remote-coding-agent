# Deployment Guide

This project supports two ways to run the same MCP server:

- Native Ubuntu with `scripts/setup-ubuntu.sh` and `scripts/up.sh`.
- Google Colab with `notebooks/remote_coding_agent_colab.ipynb`.

Docker, Docker Compose, public reverse tunnels, and manually exposed MCP ports are not supported deployment paths in this repository.

## 1. Security model

The server provides one `run_command` tool. It executes arbitrary shell commands under the operating-system identity that starts the server. Treat access to the MCP tool as access to a shell running as that user.

Both supported deployment paths run the MCP server without MCP bearer authentication and bind it to loopback (`127.0.0.1`). OpenAI Secure MCP Tunnel is the only remote connection path. `tunnel-client` authenticates to OpenAI with the runtime API key. Do not replace this with a public Quick Tunnel, ngrok, or a direct public listener.

The configured workspace is the default current directory and the base for the `working_directory` parameter. It is not a sandbox or a filesystem access-control boundary. Shell commands can access other paths permitted to the server's user. On your personal Ubuntu account, that may include `~/Documents`, `~/Downloads`, SSH configuration, and other private files. Use a dedicated Linux account or a VM if you need stronger separation. Do not run this service as root.

The Ubuntu launcher runs the MCP server as your normal user, but runs `tunnel-client` under a separate locked system account (`remote-coding-agent-tunnel`). The Colab notebook does not have this separate-account boundary: both processes run under the same Colab runtime identity. Avoid sensitive files and valuable credentials in Colab, and revoke the runtime key when finished if you are concerned it may have been exposed. On Ubuntu, the dashboard is log-only and does not receive credentials. The launcher prompts for the Tunnel ID and runtime API key in the terminal; the API key is entered without terminal echo and is piped to the tunnel supervisor over stdin. It is not saved in a dashboard config or persistent credentials file. The tunnel client receives it in its process environment. The launcher clears the normal user's cached sudo authorization after tunnel startup. Shutdown uses a fixed-purpose root-owned cleanup helper that can terminate only processes belonging to the tunnel service account. Root can still inspect both processes, and a user can regain sudo privileges by authenticating, so do not enter your sudo password in response to an unexpected prompt while the remote tool is active.

## 2. Native Ubuntu: first-time setup

Supported platform: Ubuntu 22.04 or newer, x86-64/amd64.

Install the small set of tools needed to clone the repository:

```bash
sudo apt update
sudo apt install -y git curl ca-certificates
```

Clone the repository and run the installer:

```bash
git clone https://github.com/Dishant402955/remote-coding-agent.git
cd remote-coding-agent
./scripts/setup-ubuntu.sh
```

For a fresh machine, `bootstrap/setup-remote-coding-agent.sh` is the one-shot setup entry point. It clones the latest `main` or fast-forwards an existing clean checkout, runs this installer, and stops. It does not start `scripts/up.sh` or create a tmux session. The setup script is safe to rerun when `scripts/up.sh` is not running. It asks for sudo authorization and then:

1. Checks the OS and CPU architecture.
2. Installs missing Ubuntu prerequisites with `apt` when needed.
3. Installs or locates `uv` without replacing Ubuntu's system Python.
4. Installs Python 3.12.16 through `uv` and synchronizes the exact dependency versions in `uv.lock`.
5. Creates the locked, non-login `remote-coding-agent-tunnel` system account and its private home directory.
6. Creates `workspace/` with owner-only permissions (`700`).
7. Downloads the pinned official OpenAI `tunnel-client` v0.0.19 release for Linux amd64.
8. Checks the archive against its pinned official SHA-256 digest before extraction, rejects unsafe archive paths, and installs `tunnel-client`, its bundled `cloudflared` helper, and release licensing metadata in the root-owned `/usr/local/lib/remote-coding-agent/tunnel-client/` directory, executable/readable by the dedicated service account.

The installer does not ask for or store your tunnel ID or runtime API key.

## 3. Start the machine and connect the existing tunnel

Run:

```bash
./scripts/up.sh
```

The launcher asks for sudo authorization, then prompts for the existing Tunnel ID and Runtime API Key in the terminal. The API key prompt does not echo the key. It starts a log-only dashboard at **http://127.0.0.1:3333** and immediately begins the MCP and tunnel startup sequence. No dashboard password, login, setup form, settings page, or Start button is involved. The dashboard streams the launcher's combined log file using Server-Sent Events and flushes each line as it is written; the same logs remain visible in the terminal. The interface is pure black with white text. To stop, press **Ctrl+C** in the launcher terminal; the cleanup handler stops the tunnel and MCP server and closes the dashboard.

The dashboard is intentionally bound to loopback only at `127.0.0.1:3333`. For a remote Ubuntu host, use SSH local port forwarding (for example, `ssh -L 3333:127.0.0.1:3333 user@host`) and then browse to `http://127.0.0.1:3333` on your local machine. Do not bind the dashboard to a public interface. The page is a monochrome, read-only log viewer. It streams launcher, MCP server, supervisor, and tunnel-client output over Server-Sent Events; it does not display command-history cards, credential forms, or lifecycle controls.

The dashboard does not store credentials. The runtime API key is not written to a persistent file by the Ubuntu launcher. It remains in the launcher's process memory while needed, is piped to the tunnel supervisor over stdin, and is exported only in the tunnel-client process environment. The MCP server receives a minimal environment without the key. The dashboard log is stored at `~/.local/state/remote-coding-agent/live.log` with owner-only permissions; it contains launcher and service logs, not the hidden terminal input.

Before starting the tunnel, the script:

1. Checks that the project environment and tunnel-client are installed.
2. Refuses to start if the selected local MCP port is already occupied.
3. Starts the MCP server on `127.0.0.1:8000` (or `REMOTE_CODING_AGENT_PORT` if set).
4. Performs MCP initialization and tool discovery.
5. Executes `printf local-mcp-ok` through the actual `run_command` tool and validates the output and exit code.
6. Runs `tunnel-client doctor --explain` with the supplied credentials as the dedicated system account.
7. Starts the Secure MCP Tunnel, then checks `/healthz`, `/readyz`, and a successful OpenAI control-plane poll before declaring readiness.

Keep the launcher running inside tmux while you use ChatGPT. Press `Ctrl+C` in that terminal to stop the tunnel and server. The launcher uses a fixed-purpose, root-owned cleanup helper to stop only processes owned by the dedicated system account, so no second sudo password prompt is needed. The dashboard does not store the password, Tunnel ID, or runtime API key.

### Starting after shutdown or reboot

The setup is persistent. After a reboot, open a terminal and run only:

```bash
cd ~/projects/remote-coding-agent   # replace this if your clone is elsewhere
./scripts/up.sh
```

Authorize sudo when prompted, then enter the existing Tunnel ID and runtime API key in the terminal. The key prompt is hidden. You do not need to reinstall Python, dependencies, or `tunnel-client` after each reboot. If you intentionally update the repository, stop the active session and rerun `./scripts/setup-ubuntu.sh` to synchronize dependencies and the pinned tunnel client.

### Optional local port override

Port 8000 is the default. To use another unused local port:

```bash
REMOTE_CODING_AGENT_PORT=8010 ./scripts/up.sh
```

The port remains bound to `127.0.0.1`. The tunnel health listener defaults to `127.0.0.1:18080`; override it with `REMOTE_CODING_AGENT_HEALTH_PORT=18081` if that port is occupied.

### Migrating from the earlier single-user Ubuntu launcher

Before using this hardened setup, stop the old MCP server and old same-user `tunnel-client` process. Revoke the runtime API key used by that earlier launcher and create a replacement key. The previous version ran both processes under the same Linux account; a `run_command` process could read the tunnel client's `CONTROL_PLANE_API_KEY` from `/proc/<pid>/environ`. The dedicated tunnel system account in this version closes that same-UID environment exposure. Do not reuse the old runtime key.

## 4. Connect from ChatGPT

Use the existing ChatGPT connector associated with the tunnel. Leave `scripts/up.sh` running while ChatGPT discovers the connector and calls `run_command`. A successful local startup alone is not proof that a ChatGPT tool call succeeded; test it from ChatGPT with a harmless command such as:

```bash
printf 'hello-from-ubuntu'
```

Expected stdout: `hello-from-ubuntu`, with exit code `0`.

## 5. Google Colab deployment

Open `notebooks/remote_coding_agent_colab.ipynb` in Google Colab and run its deployment cell. The notebook:

- Preflights the Colab runtime.
- Clones or fast-forward updates this repository.
- Installs the Python package and notebook dependencies.
- Downloads and verifies the official tunnel-client asset for the runtime architecture.
- Starts the MCP server on loopback with MCP authentication disabled.
- Initializes a real MCP client, checks tool discovery, and executes a harmless command.
- Runs tunnel diagnostics, starts the tunnel-client, and checks local health, readiness, and control-plane polling.

Run the notebook's cleanup cell to stop both processes and clear the API key from notebook variables. Colab runtime storage is ephemeral. Re-enter your tunnel ID and runtime key after a runtime reset.

## 6. Health checks and logs on Ubuntu

The tunnel health listener binds to loopback port `18080` by default. While `scripts/up.sh` is running, check it from another terminal:

```bash
curl -fsS http://127.0.0.1:18080/healthz; echo
curl -fsS http://127.0.0.1:18080/readyz; echo
curl -fsS http://127.0.0.1:18080/health/control-plane; echo
```

Expected responses are `live` and `ready`. The control-plane JSON should contain `"status":"ok"`, a non-empty `details.last_success`, and `details.consecutive_failures` equal to `0`. Override the health listener with `REMOTE_CODING_AGENT_HEALTH_PORT=18081` if port 18080 is already occupied.

The MCP readiness endpoints are `http://127.0.0.1:8000/healthz` and `/readyz`. The tunnel-client health listener is `http://127.0.0.1:18080/healthz`, `/readyz`, and `/health/control-plane`.

The log dashboard is available at `http://127.0.0.1:3333` while the launcher is running. It displays the combined launcher, MCP server, supervisor, and tunnel-client log stream in near real time using Server-Sent Events. The stream resumes from the browser's last delivered byte offset after a reconnect. The private combined log is `~/.local/state/remote-coding-agent/live.log`. Individual service logs remain under `/tmp/` and the tunnel account's files remain restricted to that account.

Follow the live MCP and command audit log from another terminal with:

```bash
tail -f /tmp/remote-coding-agent-server-child.log
```

Follow supervisor lifecycle transitions with:

```bash
tail -f /tmp/remote-coding-agent-server-supervisor.log
```

Tunnel logs are `/tmp/remote-coding-agent-tunnel-client.log` and `/tmp/remote-coding-agent-tunnel-supervisor.log`; they are owned by the isolated tunnel service account, so inspect them with `sudo tail -40 <path>`.

## 7. Troubleshooting

**`Port 8000 is already accepting connections`**

A server or another process is already listening. Inspect it with:

```bash
ss -ltnp 'sport = :8000'
```

Stop the old process or select another port with `REMOTE_CODING_AGENT_PORT=8010`.

**`Python environment missing` or `tunnel-client missing`**

Run `./scripts/setup-ubuntu.sh` again from the repository root.

**`tunnel-client doctor` reports a credential or permission failure**

Confirm the runtime API key is active and its principal has Tunnels Read and Use for the existing tunnel. This key is not an admin API key. Do not paste it into chat or commit it to a file.

**Tunnel starts but ChatGPT cannot discover the connector**

Keep `scripts/up.sh` running, check that the tunnel is associated with the intended ChatGPT workspace, and verify the connector uses the same tunnel. Check `/healthz` and `/readyz`; review the tunnel-client output for control-plane or MCP errors.

**The local MCP endpoint returns HTTP 406 to a plain GET**

That is expected for Streamable HTTP: an MCP client must send the protocol's required `Accept` headers and initialize a session. Use the launcher's MCP smoke test or a real MCP client rather than treating a plain browser GET as an MCP health check.

## 8. Updating the repository

From the repository root:

```bash
git pull --ff-only
./scripts/setup-ubuntu.sh
```

Then restart with `./scripts/up.sh`. Review the diff before pulling if you have made local changes.


## 9. Long-running operation and self-healing

The native Ubuntu launcher is configured for long-running sessions:

- MCP sessions may remain idle for up to seven days (604800 seconds); the default session capacity is 256.
- Commands have a 120-second default timeout and a 3600-second maximum.
- Four commands execute concurrently by default. A bounded queue holds up to 64 additional commands, with a 30-second wait limit.
- The MCP supervisor checks the exact `/readyz` endpoint. It does not treat a generic HTTP 404 as healthy. After three consecutive failures following startup grace, it terminates the whole process group and restarts the server.
- The tunnel supervisor checks `/healthz`, `/readyz`, and successful OpenAI control-plane polling. After three consecutive misses following startup grace, it replaces the tunnel client.
- Both supervisors use exponential restart backoff from 2 to 60 seconds and reset the delay after a stable five-minute run. They print concise state transitions to the foreground console and retain bounded status/client logs under `/tmp/`.
- Server output is copy-truncated at 10 MiB to keep a bounded current/previous log pair. Tunnel-client output retains up to three 10 MiB rotations; the dedicated tunnel account owns these files, so inspect them with `sudo`.
- The launcher holds a `flock` single-instance lock and requests a systemd inhibitor for sleep, idle, and lid-close while it is running. A seven-day run still requires the host to remain powered, connected to the network, and not manually shut down.

To keep the launcher attached to a persistent terminal across SSH disconnects:

```bash
tmux new -s remote-agent ./scripts/up.sh
```

Detach with `Ctrl+B`, then `D`. Reattach with `tmux attach -t remote-agent`. To stop it, reattach and press `Ctrl+C`. The launcher intentionally remains in the foreground inside tmux so you can observe readiness and stop it cleanly.

### Run the reliability soak test

The harness repeatedly initializes MCP sessions, exercises command execution, verifies timeout and output-truncation behavior, reconnects clients periodically, and optionally monitors tunnel health:

```bash
cd ~/projects/remote-coding-agent
uv run python scripts/soak-test.py \
  --url http://127.0.0.1:8000/mcp \
  --health-url http://127.0.0.1:18080 \
  --duration-seconds 604800 \
  --concurrency 8 \
  --report /tmp/remote-agent-soak-report.json
```

The default duration is 604800 seconds (seven days). Run it in another tmux window while the agent is serving requests. It exits non-zero on command failures, transport failures, or failed health checks. Seven-day configuration is not proof of seven-day stability: qualification still requires completing the soak test on the target machine and reviewing its report. Colab remains an ephemeral runtime and is not covered by the Ubuntu process supervisors.
