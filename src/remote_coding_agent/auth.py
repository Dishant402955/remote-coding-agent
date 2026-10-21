"""Bearer-token authentication middleware for the remote MCP HTTP endpoint."""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any

ASGIApp = Callable[
    [
        dict[str, Any],
        Callable[..., Awaitable[Any]],
        Callable[..., Awaitable[Any]],
    ],
    Awaitable[None],
]


def token_matches(authorization: str | None, expected_token: str) -> bool:
    """Return whether an Authorization header contains the configured bearer token."""
    if not authorization:
        return False
    scheme, separator, supplied_token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not supplied_token:
        return False
    return hmac.compare_digest(
        supplied_token.encode("utf-8"), expected_token.encode("utf-8")
    )


class BearerTokenMiddleware:
    """Require one valid bearer credential for every HTTP request."""

    def __init__(self, app: ASGIApp, expected_token: str) -> None:
        if not expected_token:
            raise ValueError("expected_token must not be empty")
        self.app = app
        self.expected_token = expected_token

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        authorization_headers = [
            value.decode("latin-1")
            for key, value in scope.get("headers", [])
            if key.lower() == b"authorization"
        ]
        # Reject duplicates rather than relying on proxy/server-specific header
        # merging rules that can disagree about which credential is authoritative.
        if (
            len(authorization_headers) == 1
            and token_matches(authorization_headers[0], self.expected_token)
        ):
            await self.app(scope, receive, send)
            return

        body = json.dumps(
            {"error": "unauthorized", "message": "A valid bearer token is required."}
        ).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"www-authenticate", b'Bearer realm="remote-coding-agent"'),
            (b"cache-control", b"no-store"),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": response_headers,
            }
        )
        await send({"type": "http.response.body", "body": body})
