from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from caida_ai_ops.itdk.config import Settings

TEST_MASTER_KEY = "test-master-key-that-is-at-least-32-bytes"
TEST_DATABASE_URL = "postgresql://test_user:test_password@127.0.0.1/test_db"

# Network-emitting Ark tests are explicitly marked ``live`` and excluded by
# default. Every ordinary test imports the capability layer in demo mode.
os.environ.setdefault("MATTHEWPP_DEMO", "1")
os.environ.setdefault(
    "MATTHEWPP_RUN_STORE",
    str(Path(tempfile.gettempdir()) / "caida-ai-ops-pytest-runs.json"),
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        master_key=TEST_MASTER_KEY,
        database_url=TEST_DATABASE_URL,
        output_dir=tmp_path,
    )


@pytest.fixture
def am():
    from caida_ai_ops import ark

    return ark


@pytest.fixture
def big_vp_pool():
    return [
        {
            "vp_id": f"vp{i}",
            "hostname": f"h{i}.example",
            "asn": 64500 + i,
            "org": "Example",
            "country": "US",
            "region": "north-america",
            "lat": 0.0,
            "lon": 0.0,
            "ipv4": True,
            "ipv6": False,
            "tags": [],
            "status": "active",
        }
        for i in range(60)
    ]


@pytest.fixture
def patched_vps(monkeypatch: pytest.MonkeyPatch, am):
    def apply(vps):
        monkeypatch.setattr(
            am,
            "list_vps",
            lambda **kwargs: {
                "status": "ok",
                "data": vps[: (kwargs.get("limit") or len(vps))],
            },
        )

    return apply
