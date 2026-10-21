# Remote Coding Agent

A self-hosted, remotely accessible coding agent that lets you direct coding work from the ChatGPT web or mobile interface. The service runs in an environment you control, exposes a small MCP interface over HTTPS, and can work with repositories, files, shell commands, tests, and Git.

> **Status:** Planning / initial project specification. This README describes the intended design; it does not claim that the implementation or security controls already exist.

## 1. Project goals

- Control a coding workspace remotely using natural-language instructions in ChatGPT.
- Run the backend on a cloud VM, a personal machine, or Google Colab where practical.
- Keep the MCP interface deliberately small: one general-purpose `run_command` tool rather than a large catalogue of narrowly specialized tools.
- Support normal development workflows: inspect a repository, edit files, install project dependencies, run linters and tests, inspect diffs, and perform Git operations including commits and pushes.
- Package the service and its runtime in Docker for reproducible deployment, while retaining a non-Docker launch path for Colab.
- Authenticate every remote request and keep the MCP access secret separate from GitHub credentials.
- Return useful, bounded command output so ChatGPT can understand results and decide what to do next.

## 2. Intended user experience

1. Start the service in the chosen runtime.
2. Expose its MCP endpoint through a trusted HTTPS URL, using a tunnel such as ngrok when the runtime has no public endpoint.
3. Configure a ChatGPT integration/connector to reach the endpoint and authenticate using the mechanism that integration supports.
4. Give ChatGPT a task in natural language, such as asking it to inspect a repository, implement a feature, run tests, and summarize the changes.
5. ChatGPT calls `run_command`; the backend validates the request, runs the requested command in the configured workspace, and returns a bounded result.
6. ChatGPT uses that result to choose the next step. This request/response loop continues until the task is complete or needs user input.
7. Review the diff and test results before committing or pushing changes.

The agent is a command-execution bridge, not a promise that every task can be completed autonomously or that every ChatGPT integration supports every MCP transport/authentication option.

## 3. Architecture

```text
ChatGPT web/mobile
       |
       | MCP request over HTTPS + configured authentication
       v
Public HTTPS endpoint / tunnel (e.g. ngrok)
       |
       v
Python MCP server
  - authenticate every request
  - validate arguments and execution limits
  - select configured workspace
  - run command and collect bounded output
       |
       v
Workspace runtime
  - repository and filesystem
  - shell / development tools
  - tests, linters, build tools
  - Git operations
       |
       +---- GitHub HTTPS/API access using a separate GitHub credential
```

### Main components

- **MCP interface:** exposes the single general-purpose `run_command` tool. Its input should include the command to execute and may include a working directory and bounded timeout/output options. The exact schema will be finalized during implementation.
- **Execution layer:** runs commands as a subprocess in the selected workspace, captures stdout/stderr and exit status, enforces timeouts, and truncates oversized output with a clear indication that it was truncated.
- **Workspace layer:** defines which directory is available to the agent. The initial design should use an explicit workspace root and avoid accidental access to unrelated host files.
- **Transport layer:** provides the MCP HTTP transport required by the chosen client and exposes it only through HTTPS when remote.
- **Authentication layer:** checks a secret on every request before invoking tools. A bearer token in the `Authorization` header is the preferred initial option if the selected ChatGPT integration supports configuring and sending that header. Integration compatibility must be verified early.
- **Git/GitHub:** Git can run in the workspace; a least-privilege GitHub token or other supported GitHub authentication method is supplied independently.
- **Deployment layer:** Docker for reproducible local/cloud deployment; a direct Python setup for Colab.

## 4. Core feature plan

### A. Remote MCP command execution

- Expose one `run_command` tool for general-purpose development work.
- Accept a command, optional workspace-relative working directory, and optional bounded execution settings.
- Return stdout, stderr, exit code, and timeout/truncation metadata.
- Support command chaining when appropriate for normal development workflows.
- Handle invalid arguments, missing executables, non-zero exit codes, timeouts, and interrupted processes cleanly.
- Keep responses compact and bounded to avoid flooding the model context with huge logs.
- Make the tool description explicit that commands execute in the configured runtime and may modify files or the environment.

**Design trade-off:** one tool keeps the tool surface and definitions small and flexible. It does not eliminate the need to limit command output, execution time, or permissions. The tool is intentionally powerful, so authentication and runtime isolation are essential.

### B. Coding workflow support

Through shell and filesystem operations, the agent should be able to:

- Inspect repository structure and relevant source files.
- Create and modify source files, configuration, and documentation.
- Install project dependencies using the project's package manager.
- Run formatters, linters, type checks, unit tests, integration tests, and build commands.
- Inspect Git status, diffs, logs, branches, and commit history.
- Create commits and push to a remote when credentials and repository permissions allow.
- Report what changed, which checks ran, their exit statuses, and any remaining failures.

The initial implementation does not need a separate tool for each of these activities. They are workflows performed through the general-purpose command interface.

### C. Authentication and secrets

- Authenticate **every incoming request** before executing a tool.
- Prefer a high-entropy bearer secret delivered in the HTTP `Authorization` header, if supported by the chosen ChatGPT integration.
- Reject missing or invalid credentials without running a command.
- Keep the MCP service secret distinct from the GitHub token.
- Load secrets from environment variables or the deployment platform's secret manager; never hard-code them in source, README examples, Docker images, logs, or committed files.
- Redact credentials from logs and errors, and document how to rotate a secret.
- Use HTTPS for remote traffic. Do not expose an unauthenticated command endpoint to the public internet.
- Verify the integration's authentication and MCP transport requirements before depending on a specific header or endpoint shape.

The MCP token grants access to the service; the GitHub token grants the permissions associated with that GitHub credential; operating-system/container permissions determine what commands can access. These are separate security boundaries.

### D. Execution safety and reliability

Arbitrary shell execution is highly privileged. The initial design should therefore include:

- A dedicated, non-root runtime user wherever possible.
- An explicit workspace root and controlled working-directory resolution.
- Per-command timeout, output-size limits, and sensible request-size limits.
- No secret values in command output or diagnostic logs where avoidable.
- Clear error handling and useful exit-status reporting.
- No public unauthenticated access; HTTPS and request authentication are mandatory for remote use.
- A documented warning that a container is not automatically a perfect security sandbox, especially if given privileged mode, host mounts, Docker socket access, or broad credentials.
- No Docker privileged mode or host filesystem mounts by default.
- Review of destructive operations and Git pushes as part of the user's workflow; later versions may add configurable confirmation policies.
- Tests for authentication rejection, timeouts, output truncation, path/workspace handling, and command failures.

A single general-purpose command tool is a deliberate product decision, not a reason to skip these controls.

### E. GitHub integration

- Work with repositories cloned into the workspace.
- Support Git status, diff, branch, commit, fetch, pull, and push via normal Git commands.
- Allow credentials to be provided through a secure runtime configuration, using least privilege and access limited to the repositories needed.
- Never place a personal access token directly in a committed remote URL or print it in logs.
- Keep GitHub authentication independent from MCP request authentication.
- Report when Git operations fail due to missing credentials, branch protection, conflicts, or permissions.

### F. Docker deployment

Provide a Docker-based deployment with:

- A Dockerfile and pinned or bounded Python dependencies.
- A non-root process user where practical.
- Environment-based configuration for the MCP secret, workspace, bind address, port, and execution limits.
- A documented volume mount for the project workspace, rather than baking user repositories into the image.
- A health check or simple readiness endpoint where compatible with the chosen MCP server design.
- A `.dockerignore` that excludes local secrets, virtual environments, caches, and unrelated files.
- Example environment configuration containing placeholders only, plus a `.env.example` that is safe to commit.
- Build and run instructions, including guidance to avoid privileged mode and unnecessary host mounts.

The image should be reproducible and should not contain personal GitHub credentials, ngrok tokens, or MCP secrets.

### G. Google Colab support

Colab is an important deployment target, but its runtime is ephemeral and its environment differs from a normal VM. Support it as a first-class launch path rather than assuming Docker is available.

- Provide a Colab notebook or clear notebook cells to install dependencies, obtain the project code, configure secrets, and start the Python MCP server.
- Support running the Python server directly in the Colab runtime without requiring Docker-in-Docker.
- Allow the server port to be exposed through an HTTPS tunnel such as ngrok when needed.
- Read tunnel and MCP secrets from Colab Secrets or secure runtime inputs, not from notebook cells committed with real values.
- Explain that a Colab runtime may disconnect, reset, or stop, and a tunnel URL may change unless the selected tunnel product/configuration supports a stable domain.
- Include a simple verification step for server startup, authentication, and a harmless command.
- Document which parts of the environment are temporary and must be reinstalled or reconfigured after a reset.

Docker remains the preferred reproducible deployment for a VM or machine where Docker is supported. Colab should use the direct Python path unless its current runtime explicitly supports the required container setup.

### H. Observability and usability

- Log request start/end, duration, exit code, timeout, and authentication failures without logging secrets.
- Give actionable error messages for connection, authentication, command, and environment failures.
- Document startup, shutdown, restart, tunnel setup, and troubleshooting.
- Return a concise command summary plus bounded output so ChatGPT can iterate effectively.

## 5. Initial API/tool contract

The initial MCP surface is intentionally minimal:

### `run_command`

Conceptual inputs:

- `command` (required): command string to execute.
- `working_directory` (optional): path within the configured workspace.
- `timeout_seconds` (optional): bounded execution timeout.
- `max_output_chars` (optional): bounded response output size.

Conceptual result:

- `stdout`
- `stderr`
- `exit_code`
- `timed_out`
- `output_truncated`
- `duration_seconds`

These are a planning contract, not a claim that the tool schema is implemented. Exact MCP SDK, transport, and response schema will be selected and tested during the implementation phase.

## 6. Proposed repository structure

```text
remote-coding-agent/
├── README.md
├── pyproject.toml             # or requirements file, depending on chosen packaging
├── Dockerfile
├── .dockerignore
├── .env.example               # placeholders only; no real secrets
├── .gitignore
├── src/
│   └── remote_coding_agent/
│       ├── __init__.py
│       ├── server.py          # MCP server and tool registration
│       ├── auth.py            # request authentication
│       ├── executor.py        # subprocess execution and limits
│       └── config.py          # environment-based configuration
├── tests/
│   ├── test_auth.py
│   ├── test_executor.py
│   └── test_server.py
└── notebooks/
    └── colab_setup.ipynb      # Colab setup and launch path
```

The structure may change as the chosen MCP SDK and transport dictate; the responsibilities should remain separated and testable.

## 7. Implementation roadmap

### Phase 0: Compatibility spike
- Confirm the current ChatGPT integration supports the chosen remote MCP transport and can authenticate requests in the required way.
- Select a Python MCP SDK and supported HTTP transport.
- Validate a minimal authenticated tool call end-to-end before building the rest.

### Phase 1: Minimal working server
- Implement configuration loading and fail safely when the required MCP secret is missing.
- Implement request authentication.
- Register the single `run_command` tool.
- Add subprocess execution, working-directory handling, timeouts, output limits, and structured results.
- Add automated tests for normal execution and failure cases.

### Phase 2: Docker
- Create the Dockerfile, non-root runtime setup, example configuration, and workspace volume instructions.
- Verify image build, startup, authentication, command execution, and clean shutdown.
- Document safe deployment defaults.

### Phase 3: Colab
- Add a Colab notebook or copyable notebook setup.
- Test direct Python startup, secret loading, tunnel startup, and a remote authenticated tool call.
- Document runtime resets, session limits, and tunnel URL behavior.

### Phase 4: Development workflow validation
- Test repository inspection, file edits, dependency installation, test execution, diff review, commit, and push.
- Confirm output is bounded and errors remain understandable.
- Test with a disposable repository before using important projects.

### Phase 5: Hardening and documentation
- Test rejection of missing/invalid credentials and confirm rejected requests cannot execute commands.
- Review path handling, subprocess termination, logging/redaction, and secret management.
- Add troubleshooting, deployment examples, and a known-limitations section.

## 8. Acceptance criteria

The initial release is ready for practical testing when:

- [ ] A supported ChatGPT integration can reach the MCP endpoint over HTTPS.
- [ ] Each request is authenticated before any command runs.
- [ ] Missing or invalid credentials are rejected.
- [ ] The single `run_command` tool executes a harmless command and returns the exit code and bounded output.
- [ ] Timeout and output truncation behavior are tested.
- [ ] The server runs locally and in a Docker container.
- [ ] The server can run directly in Colab without requiring Docker-in-Docker.
- [ ] A tunnel can expose the endpoint for remote testing without exposing an unauthenticated service.
- [ ] A test repository can be inspected, modified, tested, committed, and pushed using explicitly configured GitHub permissions.
- [ ] No real secrets are committed or included in the Docker image.
- [ ] Documentation clearly explains runtime persistence, authentication setup, and security limitations.

## 9. Configuration principles

Configuration will be environment-driven. Likely settings include:

- MCP authentication secret.
- Bind host and port.
- Workspace root.
- Default and maximum command timeout.
- Maximum captured output size.
- Optional tunnel configuration, managed by the tunnel tool/platform.
- GitHub credential configuration, supplied separately and only when needed.

Actual variable names and defaults will be finalized during implementation. Example files must use placeholder values only.

## 10. Known constraints and open questions

- ChatGPT custom-integration support for remote MCP, HTTP transport, and custom authentication headers must be verified against the actual integration configuration available to the user.
- Colab is not a persistent server host. Sessions and local files can disappear when the runtime resets, and free-tier availability/limits can change.
- ngrok and other tunnel providers may require accounts, tokens, or a paid plan for stable domains.
- A remote command-execution service can modify or delete files and access whatever the runtime identity can access. Only run it in an environment and workspace you are prepared to trust with that authority.
- Docker improves reproducibility and can limit exposure, but is not by itself a complete security boundary.
- The first version focuses on reliable remote command execution and development workflows, not on implementing a separate code-generation model. ChatGPT provides the natural-language reasoning/orchestration, while the remote runtime performs the commands.

## 11. Non-goals for the first version

- Building a full IDE or browser-based editor.
- Replacing GitHub or ChatGPT's own interface.
- Supporting every possible deployment platform with identical persistence guarantees.
- Providing unrestricted access to the host machine by default.
- Claiming that every command is safe or that all coding tasks can run without human review.

## 12. Definition of success

From ChatGPT on a phone or browser, the user can instruct the agent to work on a repository in the configured remote workspace. The agent can inspect files, make changes, run checks, show a bounded summary and diff, and perform Git operations when explicitly configured. The same core Python server can run in Docker on a normal machine/VM and directly in Colab, with authentication and deployment differences documented clearly.


## 13. Phase 0 compatibility findings (checked 2026-10-21)

This section records verified platform constraints before implementation. The backend can still be built and tested independently, but the planned *native ChatGPT connection* is gated by the user's plan and client surface.

### Verified constraints

1. **Native custom MCP app eligibility:** OpenAI's current documentation describes full custom MCP support, including write/modify actions, for ChatGPT Business, Enterprise, and Edu. Pro is documented as having custom MCP connections with read/fetch permissions only. ChatGPT Go is not listed as supporting custom MCP apps, so this project must not assume that a Go account can connect this server as a native custom MCP app. See [Connecting custom MCP servers in ChatGPT](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) and [Creating custom MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-creating-custom-mcp-apps-in-chatgpt).

2. **Mobile limitation:** OpenAI currently documents custom MCP apps as web-only, not available in the ChatGPT mobile apps. This means the exact goal of invoking the server from the native ChatGPT phone app is not currently supported by the documented native custom-MCP path. The backend itself should remain client-independent so another compatible MCP client can be used for testing or development. Source: [Creating custom MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-creating-custom-mcp-apps-in-chatgpt).

3. **GPT Actions are not an assumed workaround:** OpenAI's current GPT documentation says personal ChatGPT accounts, including Go, cannot create or publish new GPTs. Existing GPTs may remain usable, subject to eligibility and permissions. We should not plan on creating a new custom GPT Action unless the account interface and current rules confirm it is available. Source: [Creating and editing GPTs](https://help.openai.com/en/articles/8554397-gpts).

4. **Authentication needs an integration-level test:** The custom-MCP setup flow asks the user to configure authentication, but the public guidance reviewed here does not establish that every account/client offers an arbitrary static bearer-token field in the exact form this service wants. Do not assume that sending `Authorization: Bearer ...` is supported until the eligible integration's configuration and a real connection test confirm it. GPT Actions have documented API-key options (Basic, Bearer, or custom header), but the availability of that route is separately gated by GPT creation permissions. Source: [Configuring actions in GPTs](https://help.openai.com/en/articles/9442513).

### Phase 0 decision

- Continue building the Python server as a standalone, authenticated MCP service. Keep it independent of a specific ChatGPT plan or interface.
- Verify Python MCP SDK/HTTP transport compatibility and local end-to-end behavior before adding Docker and Colab packaging.
- Treat a native ChatGPT app connection as an external compatibility gate, not as something already guaranteed by this codebase.
- Until account eligibility and mobile support change or are otherwise confirmed, test the backend with a compatible MCP client and document that this does **not** prove native ChatGPT mobile integration.
- Do not remove authentication or expose command execution publicly to work around client limitations.

These findings describe the published product documentation at the date above; ChatGPT plans and app support can change, so re-check the official guidance when setting up the final integration.


## 14. Phase 1 implementation progress

### Task: authenticated MCP server and command runner

The first implementation slice adds the Python server entry point, a Streamable HTTP MCP app, a bearer-token ASGI middleware, a bounded shell executor, and automated tests. The HTTP middleware checks every HTTP request before it reaches the MCP application. Missing or invalid tokens receive HTTP 401; the middleware uses constant-time token comparison.

The initial `run_command` tool accepts a command, an optional workspace-relative directory, and an optional timeout. It returns stdout, stderr, exit status, timeout state, truncation state, and elapsed time. The executor rejects empty commands, invalid timeouts, and working directories outside the configured workspace. Output is bounded by configuration.

**Security boundary:** the workspace check constrains the process working directory, not every filesystem operation a shell command can perform. Shell commands can still read or modify anything the operating-system identity can access. Run this service in a dedicated account/container with limited credentials and expose it remotely only over HTTPS.

### Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
export MCP_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export REMOTE_CODING_AGENT_WORKSPACE="$PWD/workspace"
remote-coding-agent
```

The MCP endpoint is served at `/mcp`. For a direct local smoke test, send the configured token as `Authorization: Bearer <token>` using a compatible MCP client. Do not expose the default local bind address directly to the internet; configure the bind host appropriately when running in Docker or behind a tunnel.

Automated checks run through GitHub Actions on pushes to `main` and pull requests.


## 15. MCP HTTP integration checks

The integration suite exercises the actual Streamable HTTP endpoint using the Starlette test client and the configured MCP application lifespan. It verifies that missing and invalid bearer tokens are rejected before MCP initialization, then initializes a session using a valid token and calls `run_command` through JSON-RPC.

Run the full suite locally with:

```bash
pip install -e '.[dev]'
pytest
```

These tests validate the in-process HTTP application and protocol flow. They do not prove that a particular ChatGPT custom integration supports sending the required Authorization header; that compatibility must be verified when connecting the deployed endpoint.
