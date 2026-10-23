# Deployment Guide

This guide covers the existing Remote Coding Agent implementation. It does not change the product architecture: the service remains a Python MCP server with one `run_command` tool.

## 1. Read this before deploying

The service executes arbitrary shell commands for a client. A command can read, write, delete, compile, install, or run anything permitted by the operating-system identity and container. Authentication only decides who can submit commands; it does not make commands safe.

`REMOTE_CODING_AGENT_AUTH_MODE` supports `bearer` (the default) and `none`. The `none` mode removes the authentication check but retains request-size limits. **Use `none` only on a private, trusted network where the endpoint cannot be reached by untrusted users. Never use it with a public Cloudflare Quick Tunnel or other public endpoint:** anyone who can reach the endpoint could run shell commands in the runtime.

**Recommended starting point:** run the Docker Compose deployment on a dedicated machine, keep the service bound to loopback, and connect only through a trusted private network or a carefully configured HTTPS reverse proxy.

Do not:
- Run the service as root on a host containing personal files.
- Use Docker privileged mode or mount `/var/run/docker.sock`.
- Mount your host home directory or unrelated host paths.
- Store broad GitHub or cloud credentials in the workspace.
- Expose port 8000 directly to the public internet.
- Use a valuable token or sensitive repository in a disposable Colab session.

## 2. Prerequisites

For Docker deployment:
- Docker Engine installed and running.
- Docker Compose plugin available as `docker compose`.
- A dedicated directory for the repository and its workspace.

For direct Python deployment:
- Python 3.11 or newer.
- `uv` installed.
- A dedicated OS account and workspace, preferably inside a VM or other isolated environment.

For Colab deployment:
- A Google account with access to Colab.
- The notebook at [`notebooks/remote_coding_agent_colab.ipynb`](notebooks/remote_coding_agent_colab.ipynb).
- Awareness that Colab is ephemeral and the Quick Tunnel creates a public HTTPS address.

## 3. Generate the MCP access token

Generate a fresh high-entropy token. Do not reuse your GitHub token as the MCP token.

Linux/macOS:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the output into a secret manager or your current shell. Do not commit it to Git, paste it into a notebook cell, or put it in the endpoint URL.

For the Docker Compose instructions below, set it in your shell:

```bash
export MCP_AUTH_TOKEN='paste-your-generated-token-here'
```

The token must be at least 32 characters. If you suspect it has been exposed, stop the service, generate a new token, update the environment, and restart.

## 4. Option A: Docker Compose (recommended)

### Step 1: Prepare the workspace

From the repository root:

```bash
mkdir -p workspace
```

Put only repositories and files that this agent is allowed to modify in this directory. The workspace directory is mounted into the container; files written there persist after the container stops.

### Step 2: Build and start

Make sure `MCP_AUTH_TOKEN` is set in the same terminal, then run:

```bash
docker compose up --build -d
```

Check the container:

```bash
docker compose ps
docker compose logs --tail=100
```

The service is published on host loopback by default at `127.0.0.1:8000`. The MCP endpoint is `http://127.0.0.1:8000/mcp`.

The container runs as a non-root user, drops Linux capabilities, uses a read-only root filesystem, limits CPU/memory/processes, and gives `/tmp` a bounded writable tmpfs. Some commands may fail if they expect to write outside the mounted workspace or `/tmp`; that is an intentional restriction.

### Step 3: Verify authentication

With the service running, check that a request without a token is rejected:

```bash
curl -i http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"deployment-check","version":"1.0"}}}'
```

Expected result: HTTP `401`. A missing token must never reach the MCP application.

Now try the same initialize request with the bearer token:

```bash
curl -i http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer ${MCP_AUTH_TOKEN}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"deployment-check","version":"1.0"}}}'
```

Expected result: HTTP `200` and an MCP initialization response. This confirms authentication and initialization only. A real tool call is covered by the automated integration tests and the notebook smoke test; it is not proof that a particular external client is compatible.

### Step 4: Connect a client

Configure your MCP client to use:

- URL: `http://127.0.0.1:8000/mcp` when the client runs on the same machine.
- Authentication: `Authorization: Bearer <your MCP token>`.

If the client is on another device, `127.0.0.1` refers to that device, not the server. Do not solve this by binding the service to every public interface without protection. Use a VPN/private network or configure a TLS reverse proxy that forwards to loopback. Keep bearer authentication enabled.

Client support for Streamable HTTP, custom bearer headers, and specific ChatGPT plans/apps varies. Test the exact client and interface you plan to use; local server tests cannot establish third-party compatibility.

### Step 5: Stop, restart, or update

Stop:
```bash
docker compose down
```

Restart:
```bash
docker compose up -d
```

Rebuild after changing source or dependencies:
```bash
docker compose up --build -d
```

The mounted `workspace/` is not removed by `docker compose down`. Back it up independently. Inspect `docker compose logs` if startup fails, but treat logs as potentially sensitive.

## 5. Option B: Run directly with Python

Direct execution is useful for development. It is less isolated than the supplied Docker deployment; use a dedicated OS account or VM rather than your everyday account.

### Step 1: Install dependencies

```bash
uv sync --extra dev
```

### Step 2: Configure the process

```bash
export MCP_AUTH_TOKEN='paste-your-generated-token-here'
export REMOTE_CODING_AGENT_HOST='127.0.0.1'
export REMOTE_CODING_AGENT_PORT='8000'
export REMOTE_CODING_AGENT_WORKSPACE="$PWD/workspace"
mkdir -p "$REMOTE_CODING_AGENT_WORKSPACE"
```

Optional limits:

```bash
export REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS=30
export REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS=300
export REMOTE_CODING_AGENT_MAX_OUTPUT_CHARS=20000
export REMOTE_CODING_AGENT_MAX_COMMAND_CHARS=16384
export REMOTE_CODING_AGENT_MAX_CONCURRENT_COMMANDS=4
```

The server validates configuration at startup. Maximums are enforced to avoid accidentally configuring unbounded command strings, output, concurrency, or timeouts.

### Step 3: Start

```bash
uv run remote-coding-agent
```

Leave the process running. The endpoint is `http://127.0.0.1:8000/mcp`. Use the authentication check in Option A to confirm it responds correctly.

### Step 4: Stop

Press `Ctrl+C` in the terminal running the server. Inspect the process list if you started it through another process manager.

## 6. Option C: Google Colab

Colab is an optional, temporary path for testing. It is not a reliable persistent server and should not be treated as a secure host for high-value credentials. The one-click notebook uses bearer authentication because its Cloudflare Quick Tunnel is publicly reachable.

1. Open [the Colab notebook](notebooks/remote_coding_agent_colab.ipynb).
2. Add `MCP_AUTH_TOKEN` to Colab Secrets. Generate a separate high-entropy value; do not reuse a GitHub credential.
3. Add `GITHUB_TOKEN` only if you need to clone a private repository. Use the minimum repository permissions and an expiry. The notebook uses it for the clone/pull step and does not intentionally forward it to the server process.
4. Run the notebook cells from top to bottom. The notebook installs dependencies, obtains the code, verifies the downloaded `cloudflared` package digest against release metadata, starts the server and temporary tunnel, and tests authentication, MCP initialization, a harmless `run_command`, and an authenticated initialization through the public tunnel.
5. Copy the printed HTTPS URL ending in `/mcp` into a compatible MCP client and configure the bearer header. Never put the token in the URL.
6. Run the notebook cleanup cell before ending the session.

The tunnel URL is public and temporary, and the Colab runtime may reset at any time. Environment filtering is not a full sandbox: commands can access files and OS resources allowed to the Colab runtime identity. Do not use this path with sensitive source code, production credentials, or valuable GitHub tokens.

The notebook verifies a local tool call and an authenticated public-tunnel initialization, but does not verify compatibility with ChatGPT. Perform a real connection test with the exact client you intend to use.

## 7. Configuration reference

| Variable | Default | Accepted range / meaning |
| --- | --- | --- |
| `REMOTE_CODING_AGENT_AUTH_MODE` | `bearer` | `bearer` or `none`; `none` is only for private trusted networks |
| `MCP_AUTH_TOKEN` | Required when mode is `bearer` | At least 32 characters; keep it secret |
| `REMOTE_CODING_AGENT_HOST` | `127.0.0.1` | Bind address; loopback is the safest default |
| `REMOTE_CODING_AGENT_PORT` | `8000` | Integer from 1 to 65535 |
| `REMOTE_CODING_AGENT_WORKSPACE` | `./workspace` | Root for relative working-directory requests |
| `REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS` | `30` | Positive; must not exceed configured maximum |
| `REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS` | `300` | 1 to 3600 seconds |
| `REMOTE_CODING_AGENT_MAX_OUTPUT_CHARS` | `20000` | 1 to 1000000 characters |
| `REMOTE_CODING_AGENT_MAX_COMMAND_CHARS` | `16384` | 1 to 65536 characters |
| `REMOTE_CODING_AGENT_MAX_CONCURRENT_COMMANDS` | `4` | 1 to 16 commands |

Common environment variable names containing token, secret, password, credential, API key, private key, access key, or authorization markers are filtered from child command environments. This is defense in depth, not a guarantee that commands cannot access credentials available through files, sockets, or the operating system.

## 8. Troubleshooting

### Container exits immediately

```bash
docker compose ps -a
docker compose logs --tail=200
```

Confirm `MCP_AUTH_TOKEN` is set in the terminal running Compose. Verify that it has at least 32 characters and is not the example placeholder.

### HTTP 401

- Check the exact token configured in the server and client.
- Send it as `Authorization: Bearer <token>`.
- Do not send multiple `Authorization` headers.
- Do not put the token in the URL or request body.

### HTTP 404 or client cannot initialize

- Confirm the endpoint ends with `/mcp`.
- Confirm the client supports MCP Streamable HTTP.
- Inspect server logs and verify the client sends the expected MCP headers.
- Check whether the client is using the server's reachable address rather than its own loopback address.

### Commands time out or output is truncated

Timeouts and output truncation are deliberate safety limits. Reduce the amount of work per command, use commands that write verbose logs to files inside the workspace, or adjust configured limits within the enforced maximums. On POSIX systems, the executor starts a new process session and kills the process group on timeout; a process that deliberately escapes its process group or is stuck in an uninterruptible kernel state may still require the runtime/container to be stopped.

### A command cannot write a file

In Docker, the root filesystem is read-only. Write project files inside the mounted workspace or temporary files under `/tmp`. Do not disable the security controls just to make a command succeed without understanding why it needs broader access.

### Colab tunnel does not start

Inspect `/content/cloudflared.log` and `/content/remote-coding-agent-server.log`. Check network availability, release metadata, and the tunnel URL detection. The notebook intentionally fails if the release metadata does not provide a SHA-256 digest to verify the downloaded package.

## 9. Run verification checks

From the repository root:

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
docker build -t remote-coding-agent:verification .
```

The GitHub Actions workflow runs these checks plus notebook JSON validation. For end-to-end deployment confidence, also run the container, verify that unauthenticated requests receive `401`, verify authenticated initialization, and test a harmless `run_command` through the actual client you plan to use.

## 10. Known security boundary

The command executor is intentionally general-purpose. It restricts the requested working directory, command size, timeout, output capture, and simultaneous executions, but it does not intercept shell commands to enforce a filesystem allowlist. Commands can still access any file, process, network endpoint, or credential that the runtime identity can access. Use OS/container isolation, least privilege, and disposable workspaces appropriate to the trust level of the commands you submit.
