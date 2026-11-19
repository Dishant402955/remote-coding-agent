# Adversarial Reliability Review

**Review date:** 2026-11-19
**Scope:** native Ubuntu launcher, MCP server, shell-command executor, Secure MCP Tunnel supervisor, bootstrap, logging, and seven-day operating profile.

This review is based on the source in this repository, the local test suite, and isolated process tests. It does not claim that the target machine has completed a seven-day soak. The current running deployment was not stopped or restarted during this review.

## Review passes

### Pass 1: command admission, concurrency, and queue exhaustion

**Attack:** submit more commands than the configured execution capacity, hold an execution slot, and continue submitting while queue entries are waiting or timing out.

**Finding and fix:** command admission is now bounded by the configured semaphore, queue capacity, and queue wait timeout. Queue reservations are released from `finally`, including timeout and cancellation paths. A full or expired queue returns a stable busy result (`exit_code=75`) rather than growing memory use or accepting an unbounded backlog.

**Evidence:** `test_command_queue_is_bounded_and_serves_waiting_work`, `test_command_queue_wait_timeout_returns_busy_result`, and the configuration bounds tests.

### Pass 2: subprocess timeout, output floods, and descendant processes

**Attack:** spawn grandchildren that inherit stdout/stderr, flood both output streams, exceed the timeout, or cancel a request while the shell is active.

**Finding and fix:** output streams are continuously drained with byte caps; timeout and cancellation kill the command process group, not only the shell wrapper. The executor strips secret-bearing environment names and `REMOTE_CODING_AGENT_*` runtime configuration before starting user commands.

**Evidence:** `test_timeout_kills_descendant_that_keeps_pipes_open`, `test_caps_large_stdout_and_stderr_without_unbounded_capture`, `test_cancellation_terminates_running_command`, `test_child_process_does_not_receive_secret_environment`, and `test_child_process_does_not_receive_runtime_configuration`.

### Pass 3: false-positive health checks and auth boundary mistakes

**Attack:** make the HTTP root return 404 while the MCP app is broken, or attempt to use health routes to bypass authentication on the MCP endpoint.

**Finding and fix:** the server now exposes explicit `GET /healthz` and `GET /readyz` routes. The server watchdog checks the exact readiness route and treats non-2xx responses as failure. Only those exact GET paths bypass optional bearer auth; POST to a health path and all MCP requests remain protected.

**Evidence:** `test_health_and_readiness_endpoints_are_explicit_and_public`, `test_public_health_get_skips_bearer_auth_only_for_exact_paths`, and `test_health_route_does_not_bypass_auth_for_other_methods_or_paths`.

### Pass 4: server crash loops, startup delay, and shutdown during backoff

**Attack:** repeatedly crash the MCP process, delay readiness during startup, or send a termination signal while the supervisor is sleeping between retries.

**Finding and fix:** the supervisor has a startup grace period, readiness-based `RUNNING` state, consecutive failure threshold, exponential backoff, and `TERM` then `KILL` process-group cleanup. Backoff sleep is tracked as a child monitor process so shutdown signals interrupt it promptly. State transitions are printed once per state change and retained in a small status log.

**Evidence:** `test_server_supervisor_restarts_crashed_child` and `test_server_supervisor_stops_promptly_during_restart_backoff`.

### Pass 5: tunnel process death, control-plane flapping, and child leakage

**Attack:** kill the tunnel client, make health/control-plane polling fail repeatedly, or leave a descendant alive after the supervisor exits.

**Finding and fix:** the tunnel supervisor checks tunnel liveness, readiness, and the last successful OpenAI control-plane poll. It tolerates startup grace, reports `DEGRADED` on the first missed check, restarts after the configured consecutive-failure threshold, and uses exponential backoff. Tunnel-client output is separated from console state messages and rotated using copy-truncate so the live process can keep its log descriptor open. Shutdown terminates the complete process group.

**Evidence:** `test_tunnel_supervisor_restarts_with_24h_ttl_and_hides_key`, `test_tunnel_supervisor_restarts_after_control_plane_health_fails`, and `test_tunnel_supervisor_terminates_child_process_group_on_shutdown`.

### Pass 6: credential leakage through argv, logs, and child environments

**Attack:** inspect command arguments, supervisor logs, tunnel-client logs, and shell-command environments for the runtime key or agent configuration.

**Finding and fix:** the runtime API key is read without terminal echo, remains a non-exported launcher variable, is sent to the dedicated tunnel account over stdin, and is exported only in that account's supervisor/client process tree. It is not put in command arguments or status logs. Tunnel logs are owned by the service account with mode `0600`; ordinary `run_command` children do not inherit the key or private `REMOTE_CODING_AGENT_*` settings.

**Evidence:** tunnel supervisor secret handling tests, environment-stripping tests, and code inspection of the launcher handoff. Root can still inspect process environments; this is not a boundary against root.

### Pass 7: seven-day resource pressure, log growth, and power-state behavior

**Attack:** leave the service running for days, fill logs, let MCP sessions idle, or allow the host to suspend during a long run.

**Finding and fix:** the native launcher sets the MCP idle-session timeout to 604800 seconds (seven days), bounds command concurrency/queue/output/timeouts, rotates server and tunnel logs, and requests a systemd inhibitor for sleep, idle, and lid-close while active. The tunnel-client uses a 24-hour maximum MCP transport connection TTL; the supervised process stays up and replaces the client after health failure. The soak-test defaults to 604800 seconds.

**Evidence:** configuration tests, log-rotation implementation, tunnel TTL test, and `scripts/soak-test.py` CLI. **Residual limit:** the seven-day soak must still be run on the target host. No local test can prove the behavior of a seven-day network/control-plane session. Power loss, manual shutdown, network loss, and reboot are outside a local process supervisor's control. The runtime key is intentionally not stored, so a reboot requires starting the launcher and entering it again.

### Pass 8: duplicate launch, clean reinstall, and operator recovery

**Attack:** run two launchers, reuse an old tmux session, clone over an existing project, or repeat the native installer.

**Finding and fix:** `up.sh` takes a per-user `flock` lock and separately checks the dedicated tunnel account and selected ports before startup. The one-shot bootstrap verifies Ubuntu/architecture, repository URL and branch, required executable files, and refuses to overwrite an existing project directory or attach to an existing `remote-agent` tmux session. The Ubuntu installer installs the tunnel supervisor under a root-owned path and reuses the validated locked service account.

**Evidence:** static review of `scripts/up.sh`, `scripts/setup-ubuntu.sh`, and `bootstrap/setup-remote-coding-agent.sh`, plus shell syntax validation. The bootstrap intentionally requires the operator to stop the old agent and remove/move the old project directory first.

### Pass 9: foreground Ctrl+C, descriptor inheritance, and orphaned log forwarders

**Attack:** press Ctrl+C while the native launcher is active, then immediately start it again; inspect every surviving process and the single-instance lock descriptor.

**Finding and fix:** the launcher log-forwarding tee now closes the launcher's lock descriptor. Both supervisors defensively close that descriptor on entry. The MCP server and its tee are launched through one dedicated process-group wrapper instead of Bash process substitution, so the supervisor can terminate and reap the complete group. The existing launcher shutdown path sends TERM first, waits for supervisor cleanup, and escalates to KILL after a bounded grace period.

**Evidence:** `test_server_supervisor_closes_inherited_lock_fd_and_reaps_process_group`, `test_dashboard_and_server_have_independent_process_groups`, and `test_interrupt_signals_exit_with_shell_signal_statuses`. These tests cover local process behavior; the final Ctrl+C qualification must still be performed on the target Ubuntu host after deployment.

## Console and log contract

The foreground console reports only meaningful transitions: `STARTING`, `RUNNING`, `DEGRADED`, `RESTARTING`, `STOPPING`, `STOPPED`, and `FAILED`. Repeated health polls do not print repeated lines while the state is unchanged. Application/client output is kept in bounded files rather than flooding the console. Tunnel logs require sudo because they are owned by the isolated tunnel account.

## Remaining qualification items

1. Run the seven-day soak from `scripts/soak-test.py` on the actual machine and review the resulting JSON report.
2. Confirm the machine stays powered, online, and does not reboot during the qualification window. The systemd inhibitor blocks automatic sleep/idle/lid-close but cannot prevent outages or deliberate shutdown.
3. Re-enter the runtime key after reboot; the bootstrap intentionally does not save it to disk.
4. A green CI run and process-level tests are evidence of correctness, not a promise that an external control plane or network cannot fail.
