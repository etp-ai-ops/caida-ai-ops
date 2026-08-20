from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from caida_ai_ops.itdk.data_access.pool import DatabasePool


class FakeCursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, Any]] = []
        self.fetched = False

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def execute(self, statement: str, parameters: Any = None) -> None:
        self.executions.append((statement, parameters))

    def fetchone(self) -> tuple[int]:
        self.fetched = True
        return (1,)


class FakeDriverPool:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.opened = False
        self.closed = False
        self.cursor = FakeCursor()

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    @contextmanager
    def connection(self) -> Any:
        yield type("Connection", (), {"cursor": lambda _: self.cursor})()


def test_pool_configuration_lifecycle_and_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[FakeDriverPool] = []

    def factory(**kwargs: Any) -> FakeDriverPool:
        pool = FakeDriverPool(**kwargs)
        captured.append(pool)
        return pool

    monkeypatch.setattr("caida_ai_ops.itdk.data_access.pool.ConnectionPool", factory)
    pool = DatabasePool("postgresql://user:password@db/name", max_size=7, query_timeout_ms=9000)
    driver = captured[0]
    assert driver.kwargs["min_size"] == 1
    assert driver.kwargs["max_size"] == 7
    assert driver.kwargs["open"] is False
    assert driver.kwargs["timeout"] == 5.0
    assert driver.kwargs["kwargs"]["application_name"] == "caida-ai-ops"
    assert driver.kwargs["kwargs"]["autocommit"] is False
    options = driver.kwargs["kwargs"]["options"]
    assert "default_transaction_read_only=on" in options
    assert "statement_timeout=9000" in options
    assert "lock_timeout=5000" in options

    pool.open()
    pool.check()
    pool.close()
    assert driver.opened and driver.closed
    assert driver.cursor.executions == [("SELECT 1", None)]
    assert driver.cursor.fetched


def test_lock_timeout_does_not_exceed_short_query_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> FakeDriverPool:
        captured.update(kwargs)
        return FakeDriverPool(**kwargs)

    monkeypatch.setattr("caida_ai_ops.itdk.data_access.pool.ConnectionPool", factory)
    DatabasePool("postgresql://user:password@db/name", max_size=1, query_timeout_ms=250)
    assert "lock_timeout=250" in captured["kwargs"]["options"]
