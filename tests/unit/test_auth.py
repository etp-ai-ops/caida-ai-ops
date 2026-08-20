from __future__ import annotations

from typing import Any

import pytest

from caida_ai_ops.itdk.auth import BearerAuthMiddleware
from tests.conftest import TEST_MASTER_KEY


async def request(
    headers: list[tuple[bytes, bytes]], *, path: str = "/mcp/sse"
) -> tuple[list[dict[str, Any]], bool]:
    called = False

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = BearerAuthMiddleware(downstream, TEST_MASTER_KEY)
    await middleware(
        {"type": "http", "method": "GET", "path": path, "headers": headers},
        receive,
        send,
    )
    return sent, called


@pytest.mark.parametrize("path", ["/mcp/sse", "/mcp/messages/"])
async def test_valid_bearer_reaches_both_mcp_legs(path: str) -> None:
    sent, called = await request([(b"authorization", f"Bearer {TEST_MASTER_KEY}".encode())], path=path)
    assert called is True
    assert sent[0]["status"] == 204


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Bearer wrong")],
        [(b"authorization", b"bearer token")],
        [(b"authorization", b"Basic token")],
        [(b"authorization", b"Bearer")],
        [(b"authorization", b"Bearer ")],
        [(b"authorization", b"Bearer token with-space")],
        [(b"authorization", b"Bearer token\x7f")],
        [(b"authorization", b"Bearer token\x80")],
        [(b"authorization", b"Bearer one"), (b"Authorization", b"Bearer two")],
    ],
)
async def test_malformed_duplicate_and_wrong_headers_are_generic_401(
    headers: list[tuple[bytes, bytes]],
) -> None:
    sent, called = await request(headers)
    assert called is False
    assert sent[0]["status"] == 401
    assert sent[1]["body"] == b'{"error":"unauthorized"}'


async def test_comparison_always_uses_equal_length_digests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparisons: list[tuple[bytes, bytes]] = []

    def compare(left: bytes, right: bytes) -> bool:
        comparisons.append((left, right))
        return False

    monkeypatch.setattr("caida_ai_ops.itdk.auth.hmac.compare_digest", compare)
    await request([])
    await request([(b"authorization", b"Bearer x")])
    assert len(comparisons) == 2
    assert all(len(left) == len(right) == 32 for left, right in comparisons)
