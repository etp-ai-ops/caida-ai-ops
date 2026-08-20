from __future__ import annotations

from pathlib import Path

import pytest

from caida_ai_ops.itdk.config import Settings
from tests.conftest import TEST_DATABASE_URL, TEST_MASTER_KEY


class FakePool:
    instance: FakePool

    def __init__(self) -> None:
        self.open_count = 0
        self.close_count = 0
        FakePool.instance = self

    @classmethod
    def from_settings(cls, settings: Settings) -> FakePool:
        return cls()

    def open(self) -> None:
        self.open_count += 1

    def close(self) -> None:
        self.close_count += 1

    def check(self) -> None:
        return None


async def test_asgi_lifespan_owns_exactly_one_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import caida_ai_ops.itdk.server as server

    pool = FakePool()
    monkeypatch.setattr(server, "configure_itdk_runtime", lambda settings: pool)
    settings = Settings(TEST_MASTER_KEY, TEST_DATABASE_URL, output_dir=tmp_path)
    app = server.create_app(settings)
    assert pool.open_count == 0
    async with app.router.lifespan_context(app):
        assert pool.open_count == 1
        assert pool.close_count == 0
    assert pool.close_count == 1


async def test_lifespan_closes_pool_when_startup_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import caida_ai_ops.itdk.server as server

    class FailingPool(FakePool):
        def open(self) -> None:
            super().open()
            raise RuntimeError("database unavailable")

    pool = FailingPool()
    monkeypatch.setattr(server, "configure_itdk_runtime", lambda settings: pool)
    app = server.create_app(Settings(TEST_MASTER_KEY, TEST_DATABASE_URL, output_dir=tmp_path))
    with pytest.raises(RuntimeError, match="database unavailable"):
        async with app.router.lifespan_context(app):
            pass
    assert pool.close_count == 1
