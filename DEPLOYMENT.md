# Deployment Guide

This project supports two ways to run the same MCP server:

- Native Ubuntu with `scripts/setup-ubuntu.sh` and `scripts/up.sh`.
- Google Colab with `notebooks/remote_coding_agent_colab.ipynb`.

Docker, Docker Compose, public reverse tunnels, and manually exposed MCP ports are not supported deployment paths in this repository.

## 1. Security model

The server provides one `run_command` tool. It executes arbitrary shell commands under the operating-system identity that starts the server. Treat access to the MCP tool as access to a shell running as that user.

Both supported deployment paths run the MCP server without MCP bearer authentication and bind it to loopback (`127.0.0.1`). OpenAI Secure MCP Tunnel is the only remote connection path. `tunnel-client` authenticates to OpenAI with the runtime API key. Do not replace this with a public Quick Tunnel, ngrok, or a direct public listener.

The configured workspace is the default current directory and the base for the `working_directory` parameter. It is not a sandbox or a filesystem access-control boundary. Shell commands can access other paths permitted to the server's user. On your personal Ubuntu account, that may include `~/Documents`, `~/Downloads`, SSH configuration, and other private files. Use a dedicated Linux account or a VM if you need stronger separation. Do not run this service as root.

The Ubuntu launcher runs the MCP server as your normal user, but runs `tunnel-client` under a separate locked system account (`remote-coding-agent-tunnel`). The Colab notebook does not have this separate-account boundary: both processes run under the same Colab runtime identity. Avoid sensitive files and valuable credentials in Colab, and revoke the runtime key when finished if you are concerned it may have been exposed. The runtime API key is read without echo, held in a non-exported shell variable, and sent to the system account over stdin; it is not placed in normal-user command arguments or environment variables. The launcher clears the user's cached sudo authorization before it reports the tunnel ready. Stopping the session asks for sudo authorization again so it can terminate the isolated tunnel account's processes. Root can still inspect both processes, and a user can regain sudo privileges by authenticating, so do not enter your sudo password in response to an unexpected prompt while the remote tool is active.

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

The setup script is safe to rerun when `scripts/up.sh` is not running. It asks for sudo authorization and then:

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

The script first asks sudo to authorize the dedicated tunnel account. It then prompts for:

- **Existing Tunnel ID** (shown while typing).
- **Runtime API Key** (hidden while typing).

It does not write either value into `.env`, shell startup files, or the repository. The MCP server runs as your normal user with a minimal environment that does not contain the key. `tunnel-client` runs as `remote-coding-agent-tunnel`; the key is sent to that account over stdin and exported only in its process environment. Once the tunnel health listener starts, the launcher clears the normal user's cached sudo authorization before waiting for OpenAI control-plane readiness.

Before starting the tunnel, the script:

1. Checks that the project environment and tunnel-client are installed.
2. Refuses to start if the selected local MCP port is already occupied.
3. Starts the MCP server on `127.0.0.1:8000` (or `REMOTE_CODING_AGENT_PORT` if set).
4. Performs MCP initialization and tool discovery.
5. Executes `printf local-mcp-ok` through the actual `run_command` tool and validates the output and exit code.
6. Runs `tunnel-client doctor --explain` with the supplied credentials as the dedicated system account.
7. Starts the Secure MCP Tunnel, then checks `/healthz`, `/readyz`, and a successful OpenAI control-plane poll before declaring readiness.

Keep the terminal open while you use ChatGPT. Press `Ctrl+C` in that terminal to stop the tunnel and server. The launcher asks for sudo authorization again to stop processes owned by the dedicated system account, then clears the sudo timestamp. It does not persist your credentials.

### Starting after shutdown or reboot

The setup is persistent. After a reboot, open a terminal and run only:

```bash
cd ~/projects/remote-coding-agent   # replace this if your clone is elsewhere
./scripts/up.sh
```

Authorize sudo when prompted, then enter the existing tunnel ID and runtime API key. You do not need to reinstall Python, dependencies, or `tunnel-client` after each reboot. If you intentionally update the repository, stop the active session and rerun `./scripts/setup-ubuntu.sh` to synchronize dependencies and the pinned tunnel client.

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

The MCP server log is at `/tmp/remote-coding-agent-server.log`.

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
