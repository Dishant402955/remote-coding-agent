"""Tests for HTTP bearer authentication."""

import asyncio
import json

from remote_coding_agent.auth import BearerTokenMiddleware, token_matches


def test_token_matches_bearer_case_insensitively() -> None:
    assert token_matches("Bearer secret-token", "secret-token")
    assert token_matches("bearer secret-token", "secret-token")


def test_token_matches_rejects_missing_wrong_scheme_and_wrong_token() -> None:
    assert not token_matches(None, "secret-token")
    assert not token_matches("secret-token", "secret-token")
    assert not token_matches("Basic secret-token", "secret-token")
    assert not token_matches("Bearer wrong-token", "secret-token")


def test_middleware_rejects_request_before_calling_app() -> None:
    called = False
    sent = []

    async def app(scope, receive, send):
        nonlocal called
        called = True

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [],
    }
    asyncio.run(BearerTokenMiddleware(app, "secret-token")(scope, receive, send))

    assert not called
    assert sent[0]["status"] == 401
    assert json.loads(sent[1]["body"])["error"] == "unauthorized"


def test_middleware_allows_valid_token() -> None:
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(b"authorization", b"Bearer secret-token")],
    }
    asyncio.run(BearerTokenMiddleware(app, "secret-token")(scope, receive, send))
    assert called
