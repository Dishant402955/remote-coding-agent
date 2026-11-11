"""End-to-end tests for the authenticated Streamable HTTP MCP endpoint."""

import asyncio
import json
from typing import Any

from starlette.testclient import TestClient

import remote_coding_agent.server as server_module
from remote_coding_agent.config import Settings
from remote_coding_agent.server import create_server

AUTH_TOKEN = "integration-test-secret-with-more-than-32-characters"
PROTOCOL_VERSION = "2025-03-26"


def _mcp_payload(response) -> dict[str, Any]:
    """Decode either JSON or server-sent-event MCP responses."""
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()

    data_lines = [
        line.removeprefix("data:").strip()
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert data_lines, f"Expected an MCP data event, got: {response.text!r}"
    return json.loads(data_lines[-1])


def _initialize(client: TestClient):
    return client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {AUTH_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "integration-test", "version": "1.0.0"},
            },
        },
    )


def test_mcp_http_rejects_missing_and_invalid_tokens(tmp_path) -> None:
    _, app = create_server(Settings(auth_token=AUTH_TOKEN, workspace=tmp_path))

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "integration-test", "version": "1.0.0"},
            },
        }
        missing = client.post(
            "/mcp",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        invalid = client.post(
            "/mcp",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer definitely-wrong",
            },
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_mcp_http_initializes_and_runs_command_with_valid_token(tmp_path) -> None:
    _, app = create_server(
        Settings(
            auth_token=AUTH_TOKEN,
            workspace=tmp_path,
            default_timeout_seconds=5,
            max_timeout_seconds=10,
        )
    )

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        initialized = _initialize(client)
        assert initialized.status_code == 200, initialized.text
        session_id = initialized.headers.get("mcp-session-id")
        assert session_id, "MCP initialization should return a session ID"

        initialize_result = _mcp_payload(initialized)
        assert initialize_result["result"]["serverInfo"]["name"] == "Remote Coding Agent"
        negotiated_version = initialize_result["result"]["protocolVersion"]

        blocked_file = tmp_path / "unauthorized-command-ran"
        unauthorized_tool_call = client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Session-Id": session_id,
                "MCP-Protocol-Version": negotiated_version,
            },
            json={
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {
                    "name": "run_command",
                    "arguments": {"command": f"touch {blocked_file}"},
                },
            },
        )
        assert unauthorized_tool_call.status_code == 401
        assert not blocked_file.exists()

        tool_response = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {AUTH_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Session-Id": session_id,
                "MCP-Protocol-Version": negotiated_version,
            },
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "run_command",
                    "arguments": {"command": "printf 'integration-ok'"},
                },
            },
        )

    assert tool_response.status_code == 200, tool_response.text
    response_payload = _mcp_payload(tool_response)
    tool_result = response_payload["result"]
    structured = tool_result.get("structuredContent")
    if structured is None:
        text_content = tool_result["content"][0]["text"]
        structured = json.loads(text_content)

    assert structured["stdout"] == "integration-ok"
    assert structured["exit_code"] == 0
    assert structured["timed_out"] is False



def test_unauthenticated_test_mode_allows_only_smoke_commands(tmp_path) -> None:
    _, app = create_server(
        Settings(
            auth_mode="none",
            test_mode=True,
            workspace=tmp_path,
            default_timeout_seconds=5,
            max_timeout_seconds=10,
        )
    )

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        initialized = client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test-mode-check", "version": "1.0.0"},
                },
            },
        )
        assert initialized.status_code == 200, initialized.text
        session_id = initialized.headers.get("mcp-session-id")
        assert session_id
        initialized_payload = _mcp_payload(initialized)
        negotiated_version = initialized_payload["result"]["protocolVersion"]

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Session-Id": session_id,
            "MCP-Protocol-Version": negotiated_version,
        }
        safe_call = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "run_command",
                    "arguments": {"command": "printf 'mcp-test-ok'"},
                },
            },
        )
        assert safe_call.status_code == 200, safe_call.text
        safe_payload = _mcp_payload(safe_call)["result"]
        safe_result = safe_payload.get("structuredContent")
        if safe_result is None:
            safe_result = json.loads(safe_payload["content"][0]["text"])
        assert safe_result["stdout"] == "mcp-test-ok"
        assert safe_result["exit_code"] == 0

        blocked_file = tmp_path / "must-not-be-created"
        blocked_call = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "run_command",
                    "arguments": {"command": f"touch {blocked_file}"},
                },
            },
        )
        assert blocked_call.status_code == 200, blocked_call.text
        blocked_payload = _mcp_payload(blocked_call)["result"]
        blocked_result = blocked_payload.get("structuredContent")
        if blocked_result is None:
            blocked_result = json.loads(blocked_payload["content"][0]["text"])
        assert blocked_result["exit_code"] == 126
        assert "test mode only permits" in blocked_result["stderr"]
        assert not blocked_file.exists()



def test_long_lived_session_capacity_is_applied_to_fastmcp(tmp_path) -> None:
    mcp, _ = create_server(
        Settings(
            auth_token=AUTH_TOKEN,
            workspace=tmp_path,
            session_idle_timeout_seconds=43200,
            max_sessions=48,
        )
    )
    assert mcp.settings.session_idle_timeout == 43200.0
    assert mcp.settings.max_sessions == 48
    assert mcp.settings.max_request_body_size == 1_048_576


def test_command_queue_is_bounded_and_serves_waiting_work(
    tmp_path, monkeypatch
) -> None:
    async def fake_execute_command(**kwargs):
        await asyncio.sleep(0.15)
        return {
            "stdout": kwargs["command"],
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "output_truncated": False,
            "duration_seconds": 0.15,
        }

    monkeypatch.setattr(server_module, "execute_command", fake_execute_command)
    mcp, _ = create_server(
        Settings(
            auth_token=AUTH_TOKEN,
            workspace=tmp_path,
            max_concurrent_commands=1,
            max_queued_commands=1,
            command_queue_timeout_seconds=2,
        )
    )

    async def invoke(command: str):
        return await mcp._tool_manager.call_tool(
            "run_command", {"command": command}, convert_result=False
        )

    async def scenario():
        first = asyncio.create_task(invoke("first"))
        await asyncio.sleep(0.02)
        second = asyncio.create_task(invoke("second"))
        await asyncio.sleep(0.02)
        rejected = await invoke("third")
        completed = await asyncio.gather(first, second)
        return completed, rejected

    completed, rejected = asyncio.run(scenario())
    assert all(item["exit_code"] == 0 for item in completed)
    assert [item["stdout"] for item in completed] == ["first", "second"]
    assert rejected["exit_code"] == 75
    assert "queue is full" in rejected["stderr"]


def test_command_queue_wait_timeout_returns_busy_result(
    tmp_path, monkeypatch
) -> None:
    async def slow_execute_command(**kwargs):
        await asyncio.sleep(1.2)
        return {
            "stdout": "done",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "output_truncated": False,
            "duration_seconds": 1.2,
        }

    monkeypatch.setattr(server_module, "execute_command", slow_execute_command)
    mcp, _ = create_server(
        Settings(
            auth_token=AUTH_TOKEN,
            workspace=tmp_path,
            max_concurrent_commands=1,
            max_queued_commands=1,
            command_queue_timeout_seconds=1,
        )
    )

    async def invoke():
        return await mcp._tool_manager.call_tool(
            "run_command", {"command": "queued"}, convert_result=False
        )

    async def scenario():
        first = asyncio.create_task(invoke())
        await asyncio.sleep(0.05)
        second = await invoke()
        await first
        return second

    result = asyncio.run(scenario())
    assert result["exit_code"] == 75
    assert "waited too long" in result["stderr"]
