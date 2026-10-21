"""Bearer-token authentication and request-size limits for the MCP HTTP endpoint."""

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
    """Require one valid bearer credential and bound every HTTP request body."""

    def __init__(
        self,
        app: ASGIApp,
        expected_token: str,
        max_request_bytes: int = 1_048_576,
    ) -> None:
        if not expected_token:
            raise ValueError("expected_token must not be empty")
        if max_request_bytes < 1:
            raise ValueError("max_request_bytes must be greater than zero")
        self.app = app
        self.expected_token = expected_token
        self.max_request_bytes = max_request_bytes

    async def _error(self, send: Callable[..., Awaitable[Any]], status: int, error: str) -> None:
        body = json.dumps({"error": error}).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ]
        if status == 401:
            headers.append((b"www-authenticate", b'Bearer realm="remote-coding-agent"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        authorization_headers = [
            value.decode("latin-1")
            for key, value in headers
            if key.lower() == b"authorization"
        ]
        if (
            len(authorization_headers) != 1
            or not token_matches(authorization_headers[0], self.expected_token)
        ):
            await self._error(send, 401, "unauthorized")
            return

        content_lengths = [
            value.decode("ascii", errors="ignore")
            for key, value in headers
            if key.lower() == b"content-length"
        ]
        if len(content_lengths) > 1:
            await self._error(send, 400, "ambiguous_content_length")
            return
        if content_lengths:
            try:
                declared_length = int(content_lengths[0])
            except ValueError:
                await self._error(send, 400, "invalid_content_length")
                return
            if declared_length < 0:
                await self._error(send, 400, "invalid_content_length")
                return
            if declared_length > self.max_request_bytes:
                await self._error(send, 413, "request_too_large")
                return

        # Buffer at most the configured request limit before dispatch. This
        # also bounds chunked requests that omit Content-Length.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_request_bytes:
                await self._error(send, 413, "request_too_large")
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)
