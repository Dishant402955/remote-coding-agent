"""Smoke-test a running container through the real MCP HTTP endpoint."""

from __future__ import annotations

import json
import os
import time

import httpx

ENDPOINT = os.getenv(
    "REMOTE_CODING_AGENT_URL", "http://127.0.0.1:18000/mcp"
)
TOKEN = os.getenv("MCP_AUTH_TOKEN", "")
PROTOCOL_VERSION = "2025-03-26"


def decode_payload(response: httpx.Response) -> dict:
    if "application/json" in response.headers.get("content-type", ""):
        return response.json()
    data_lines = [
        line.removeprefix("data:").strip()
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    if not data_lines:
        raise RuntimeError(
            f"Expected an MCP data event, got HTTP {response.status_code}"
        )
    return json.loads(data_lines[-1])


def main() -> None:
    if len(TOKEN) < 32:
        raise RuntimeError("MCP_AUTH_TOKEN must be set to a test token of at least 32 characters")

    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    initialize_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "container-smoke-test", "version": "1.0"},
        },
    }

    with httpx.Client(timeout=10) as client:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                probe = client.post(
                    ENDPOINT,
                    json=initialize_payload,
                    headers={"Content-Type": "application/json"},
                )
                if probe.status_code == 401:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            raise RuntimeError("Container did not become ready within 20 seconds")

        unauthorized = client.post(
            ENDPOINT,
            json=initialize_payload,
            headers={"Content-Type": "application/json"},
        )
        if unauthorized.status_code != 401:
            raise RuntimeError("Unauthenticated request was not rejected")

        initialized = client.post(
            ENDPOINT, json=initialize_payload, headers=headers
        )
        if initialized.status_code != 200:
            raise RuntimeError(
                f"Authenticated initialize failed: HTTP {initialized.status_code}"
            )
        session_id = initialized.headers.get("mcp-session-id")
        if not session_id:
            raise RuntimeError("MCP initialize response omitted its session ID")
        negotiated = decode_payload(initialized)["result"]["protocolVersion"]

        session_headers = {
            **headers,
            "MCP-Session-Id": session_id,
            "MCP-Protocol-Version": negotiated,
        }
        client.post(
            ENDPOINT,
            headers=session_headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        tool_response = client.post(
            ENDPOINT,
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "run_command",
                    "arguments": {"command": "printf container-smoke-ok"},
                },
            },
        )
        if tool_response.status_code != 200:
            raise RuntimeError(
                f"run_command failed: HTTP {tool_response.status_code}"
            )

    result = decode_payload(tool_response)["result"]
    structured = result.get("structuredContent")
    if structured is None:
        structured = json.loads(result["content"][0]["text"])
    if structured.get("stdout") != "container-smoke-ok":
        raise RuntimeError(f"Unexpected command output: {structured.get('stdout')!r}")
    if structured.get("exit_code") != 0 or structured.get("timed_out"):
        raise RuntimeError(f"Command execution failed: {structured}")
    print("Container authentication, MCP initialization, and run_command passed.")


if __name__ == "__main__":
    main()
