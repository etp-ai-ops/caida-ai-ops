"""Strict bearer authentication for the complete MCP ASGI surface."""

from __future__ import annotations

import hashlib
import hmac

from starlette.types import ASGIApp, Receive, Scope, Send


class BearerAuthMiddleware:
    """Reject every HTTP request whose single bearer credential is not exact."""

    def __init__(
        self,
        app: ASGIApp,
        master_key: str,
        excluded_paths: frozenset[str] = frozenset(),
    ) -> None:
        self._app = app
        self._expected_digest = hashlib.sha256(master_key.encode("ascii")).digest()
        self._excluded_paths = excluded_paths

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or scope.get("path") in self._excluded_paths:
            await self._app(scope, receive, send)
            return

        credentials = [value for name, value in scope.get("headers", ()) if name.lower() == b"authorization"]
        supplied = b""
        syntax_valid = len(credentials) == 1 and credentials[0].startswith(b"Bearer ")
        if syntax_valid:
            supplied = credentials[0][len(b"Bearer ") :]
            syntax_valid = bool(supplied) and not any(byte <= 0x20 or byte >= 0x7F for byte in supplied)

        supplied_digest = hashlib.sha256(supplied).digest()
        authenticated = hmac.compare_digest(supplied_digest, self._expected_digest)
        if not syntax_valid or not authenticated:
            body = b'{"error":"unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self._app(scope, receive, send)
