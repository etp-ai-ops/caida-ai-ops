"""The output contract and VP filtering — the parts every consumer (CLI,
notebook, MCP client) depends on being stable."""

from __future__ import annotations

import os

import pytest

ENVELOPE_KEYS = {"status", "function", "parameters", "data", "warnings", "provenance"}


def test_success_envelope_shape(am):
    r = am.list_vps(region="africa")
    assert set(r) >= ENVELOPE_KEYS
    assert r["status"] == "ok"
    assert isinstance(r["data"], list)


def test_error_envelope_shape(am):
    """Errors keep the same envelope. A consumer must never have to tell a
    crash from a rejection by parsing a stack trace."""
    r = am.ping(target="a.example", vp_filter={"region": "africa"}, count=999)
    assert set(r) >= ENVELOPE_KEYS
    assert r["status"] == "error"
    assert r["data"] is None
    assert r["error"]["message"]


def test_demo_responses_are_labelled_as_synthetic(am):
    """Synthetic data must announce itself, or it will eventually be quoted as
    a real measurement."""
    r = am.ping(target="a.example", vp_filter={"region": "africa"}, count=2)
    assert any("DEMO" in w.upper() for w in r["warnings"])


def test_parameters_are_echoed_for_provenance(am):
    r = am.ping(target="host.example", vp_filter={"region": "africa"}, count=5)
    assert r["parameters"]["target"] == "host.example"
    assert r["parameters"]["count"] == 5


# --- filtering -------------------------------------------------------------


def test_filters_are_applied(am):
    assert all(v["country"] == "ZA" for v in am.list_vps(country="ZA")["data"])
    assert all(v["region"] == "africa" for v in am.list_vps(region="africa")["data"])


def test_country_filter_is_case_insensitive(am):
    assert len(am.list_vps(country="za")["data"]) == len(am.list_vps(country="ZA")["data"])


def test_tag_filter_selects_only_tagged_vps(am):
    for v in am.list_vps(tag="cloud")["data"]:
        assert "cloud" in v["tags"]


def test_unmatched_filter_errors_rather_than_falling_back(am):
    """A filter matching nothing must never quietly fall back to the full
    fleet — that would turn a typo into a 383-VP measurement. It raises
    NoMatchingVPsError, which is louder (and better) than an empty list."""
    r = am.list_vps(country="XX")
    assert r["status"] == "error"
    assert r["error"]["type"] == "NoMatchingVPsError"
    assert r["data"] is None


def test_limit_is_respected(am):
    assert len(am.list_vps(limit=2)["data"]) == 2


def test_nearest_vps_are_distance_sorted(am):
    vps = am.nearest_vps(lat=51.5, lon=-0.13, n=5)["data"]
    dists = [v["_distance_km"] for v in vps if "_distance_km" in v]
    assert dists == sorted(dists), "nearest_vps must return closest-first"


def test_nearest_vps_respects_n(am):
    assert len(am.nearest_vps(lat=51.5, lon=-0.13, n=3)["data"]) <= 3


# --- partial-failure handling ----------------------------------------------


def test_aggregators_tolerate_unmeasurable_vps(am, monkeypatch):
    """_live_measure emits a record for every VP asked about, and a VP that
    errored carries `error` instead of measurement fields. Aggregators must
    skip those rather than KeyError on the first one."""
    mixed = {
        "status": "ok",
        "data": [
            {"vp_id": "a", "received": 2, "sent": 2, "rtt_avg_ms": 5.0},
            {"vp_id": "b", "error": "no result returned within 25s"},
        ],
        "warnings": [],
    }
    monkeypatch.setattr(am, "ping", lambda *a, **k: mixed)
    out = am.is_reachable(target="x", vp_filter={"region": "africa"})
    assert out["status"] == "ok"
    per_vp = {r["vp_id"]: r["reachable"] for r in out["data"]["per_vp"]}
    assert per_vp["a"] is True
    # unmeasurable is None, not False: a failed measurement is not evidence
    # that the target is down.
    assert per_vp["b"] is None
    assert out["data"]["pct_reachable"] == 100.0


def test_discovery_failure_surfaces_its_real_reason(am, monkeypatch):
    """A failed list_vps returns data: None. Indexing into it turned a clear
    'no VPs matched' into an opaque TypeError several frames later."""
    monkeypatch.setattr(
        am,
        "list_vps",
        lambda **k: {
            "status": "error",
            "data": None,
            "error": {"type": "NoMatchingVPsError", "message": "no active Ark VPs matched"},
        },
    )
    r = am.ping(target="x", vp_filter={"country": "ZZ"}, count=1)
    assert r["status"] == "error"
    assert "no active Ark VPs matched" in r["error"]["message"]


# --- Ark mux availability --------------------------------------------------


def test_missing_mux_socket_says_so_and_how_to_fix_it(am, tmp_path):
    """Three causes get conflated into one unhelpful message otherwise, and
    they need three different fixes. Absent socket = not on an Ark host, or the
    container did not bind-mount it."""
    with pytest.raises(am.ArkMuxUnavailableError) as exc:
        am._check_mux_access(tmp_path / "nope")
    msg = str(exc.value)
    assert "no Ark mux socket" in msg
    assert "bind-mount" in msg and "MATTHEWPP_DEMO" in msg


def test_non_socket_path_is_reported_distinctly(am, tmp_path):
    """A container bind-mount of a host path that does not exist silently
    creates a directory — a confusing failure worth naming exactly."""
    stray = tmp_path / "mux"
    stray.mkdir()
    with pytest.raises(am.ArkMuxUnavailableError, match="not a unix socket"):
        am._check_mux_access(stray)


def test_unreadable_socket_names_the_group_and_gid(am, tmp_path):
    """Not being in ark-mux is the most common live failure. The message must
    carry the numeric gid, since that is what crosses a container boundary."""
    import socket as _socket

    sock_path = tmp_path / "mux"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        srv.bind(str(sock_path))
        sock_path.chmod(0o000)
        if os.access(sock_path, os.R_OK | os.W_OK):
            pytest.skip("running as root: permission checks do not apply")
        with pytest.raises(am.ArkMuxUnavailableError) as exc:
            am._check_mux_access(sock_path)
        msg = str(exc.value)
        assert "permission denied" in msg
        assert "group_add" in msg and "getent group" in msg
    finally:
        srv.close()


def test_accessible_socket_passes(am, tmp_path):
    import socket as _socket

    sock_path = tmp_path / "mux"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        srv.bind(str(sock_path))
        am._check_mux_access(sock_path)  # must not raise
    finally:
        srv.close()
