"""End-to-end tests for the authenticated Streamable HTTP MCP endpoint."""

import json
from typing import Any

from starlette.testclient import TestClient

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
