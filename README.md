# Remote Coding Agent

Remote Coding Agent is a self-hosted MCP server that exposes one general-purpose `run_command` tool. A compatible MCP client can use it to run development commands in a workspace hosted on your machine, VM, or a supported notebook runtime.

The project deliberately keeps the tool surface small. That also means commands can modify files and do anything permitted to the server's operating-system identity. **Treat this service as remote code execution, not as a safe read-only assistant.**

## Features

- Python MCP server using Streamable HTTP.
- Optional bearer-token authentication; the default Docker deployment requires a token.
- Configurable workspace, timeout, command length, output size, and concurrent execution limits.
- Bounded stdout/stderr capture and process-group termination on timeout on POSIX systems.
- Common credential-bearing environment variables are filtered from child command environments.
- Non-root Docker image and a hardened Docker Compose configuration.
- Optional Google Colab notebook for ephemeral remote testing.

## Quick start (Docker)

Requirements: Docker Engine with the Compose plugin.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
export MCP_AUTH_TOKEN='paste-the-generated-secret-here'
mkdir -p workspace
docker compose up --build -d
docker compose ps
docker compose logs --tail=100
```

The Compose configuration binds to `127.0.0.1` by default. The MCP endpoint is:

```text
http://127.0.0.1:8000/mcp
```

Keep it on loopback unless you have deliberately configured a trusted private network or HTTPS reverse proxy. Do not expose a command-execution endpoint directly to the public internet.

**Full instructions:** [Deployment Guide](DEPLOYMENT.md)

## Configuration

All settings are read from environment variables. Bearer mode requires an MCP token with at least 32 characters of high-entropy secret material. For the Colab Secure MCP Tunnel flow, the MCP server runs without its own token on loopback and tunnel-client authenticates to OpenAI.

| Variable | Default | Purpose |
| --- | --- | --- |
| `MCP_AUTH_TOKEN` | Required | Bearer token for the MCP HTTP endpoint |
| `REMOTE_CODING_AGENT_HOST` | `127.0.0.1` | Server bind address |
| `REMOTE_CODING_AGENT_PORT` | `8000` | Server port |
| `REMOTE_CODING_AGENT_WORKSPACE` | `./workspace` | Workspace root and default working directory |
| `REMOTE_CODING_AGENT_DEFAULT_TIMEOUT_SECONDS` | `30` | Default per-command timeout |
| `REMOTE_CODING_AGENT_MAX_TIMEOUT_SECONDS` | `300` | Maximum per-command timeout, capped at 3600 |
| `REMOTE_CODING_AGENT_MAX_OUTPUT_CHARS` | `20000` | Maximum combined returned output, capped at 1000000 |
| `REMOTE_CODING_AGENT_MAX_COMMAND_CHARS` | `16384` | Maximum command string length, capped at 65536 |
| `REMOTE_CODING_AGENT_MAX_CONCURRENT_COMMANDS` | `4` | Concurrent commands, configurable from 1 to 16 |
| `REMOTE_CODING_AGENT_MAX_REQUEST_BYTES` | `1048576` | Request body limit, configurable from 1 to 10485760 bytes |

## Development and checks

Requirements: Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
docker build -t remote-coding-agent:local .
```

The CI workflow runs lint, tests, a Docker image build, and notebook JSON validation. Notebook JSON validation alone does not execute a live Colab session. Check the Actions run for the commit you intend to use.

## Security model and limitations

- **Authentication is not a sandbox.** The bearer token protects HTTP access; it does not limit what an authenticated command can do.
- **Workspace paths restrict the selected working directory, not all filesystem access.** A shell command may read or modify any path allowed to the process identity.
- **Environment filtering is defense in depth.** Common token/secret/password/key variables and Git askpass configuration are removed from child environments, but this cannot protect credentials stored in readable files, accessible sockets, or other OS resources.
- **Use a dedicated runtime.** Do not run this service as a privileged user on a personal machine with broad access to private files. The Docker deployment is safer than running it directly on a host, but Docker is not a perfect security boundary.
- **Credentials must be least-privilege.** Never mount the Docker socket or the host home directory. Avoid placing Git credentials in the workspace. Rotate any credential that may have been exposed to commands.
- **Remote traffic must be encrypted.** The default Compose binding is loopback. For remote use, prefer a VPN/private network or a correctly configured HTTPS reverse proxy, and retain bearer authentication.
- **Colab is temporary and public-facing when tunneled.** Do not use valuable secrets or sensitive workspaces there. Runtime resets erase local state and quick-tunnel URLs change.
- **Client compatibility is separate from server functionality.** Passing local MCP tests does not prove that a specific ChatGPT plan, mobile app, or third-party client supports the endpoint and authentication configuration.

## Repository layout

```text
src/remote_coding_agent/  Server, configuration, authentication, command executor
tests/                    Unit and MCP HTTP integration tests
notebooks/                Optional Colab deployment notebook
Dockerfile                Non-root container image
compose.yaml              Local deployment defaults and resource controls
DEPLOYMENT.md             Step-by-step deployment and troubleshooting
```
