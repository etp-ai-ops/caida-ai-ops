"""The hard limits are the safety-critical part of this toolkit: they are what
stops an agent from turning ~383 globally-distributed vantage points into a
distributed flood. Every limit is tested at its exact boundary (accept at N,
reject at N+1), because an off-by-one here is the difference between a bounded
measurement and an incident.

Limits must *reject*, never silently clamp — a clamped request returns data
that does not match what was asked for, which is worse than an error.
"""

from __future__ import annotations

import pytest


def _err(resp) -> str:
    assert resp["status"] == "error", f"expected rejection, got {resp['status']}"
    return resp["error"]["message"]


# --- ping ------------------------------------------------------------------


@pytest.mark.parametrize("value,ok", [(60.0, True), (60.1, False), (0.001, True), (0.0, False)])
def test_ping_duration_bounds(am, value, ok):
    r = am.ping(target="a.example", vp_filter={"region": "africa"}, duration_s=value)
    assert (r["status"] == "ok") is ok


@pytest.mark.parametrize("value,ok", [(120, True), (121, False), (1, True), (0, False)])
def test_ping_count_bounds(am, value, ok):
    r = am.ping(target="a.example", vp_filter={"region": "africa"}, count=value)
    assert (r["status"] == "ok") is ok


@pytest.mark.parametrize("value,ok", [(200, True), (199, False)])
def test_ping_interval_floor(am, value, ok):
    """interval_ms has a floor, not a ceiling: it is what keeps a single VP
    from flooding the target."""
    r = am.ping(target="a.example", vp_filter={"region": "africa"}, count=3, interval_ms=value)
    assert (r["status"] == "ok") is ok


@pytest.mark.parametrize("value,ok", [(100, True), (99, False), (10_000, True), (10_001, False)])
def test_ping_timeout_bounds(am, value, ok):
    r = am.ping(target="a.example", vp_filter={"region": "africa"}, count=3, timeout_ms=value)
    assert (r["status"] == "ok") is ok


# --- traceroute ------------------------------------------------------------


@pytest.mark.parametrize("value,ok", [(64, True), (65, False), (1, True), (0, False)])
def test_traceroute_hop_bounds(am, value, ok):
    r = am.traceroute(target="a.example", vp_filter={"region": "africa"}, max_hops=value)
    assert (r["status"] == "ok") is ok


@pytest.mark.parametrize("value,ok", [(5, True), (6, False)])
def test_traceroute_attempts_bounds(am, value, ok):
    r = am.traceroute(target="a.example", vp_filter={"region": "africa"}, attempts_per_hop=value)
    assert (r["status"] == "ok") is ok


@pytest.mark.parametrize("value,ok", [(100, True), (99, False), (5_000, True), (5_001, False)])
def test_traceroute_wait_bounds(am, value, ok):
    r = am.traceroute(target="a.example", vp_filter={"region": "africa"}, wait_ms=value)
    assert (r["status"] == "ok") is ok


# --- fan-out and aggregate probe budget ------------------------------------


@pytest.mark.parametrize(
    "fn,kwargs",
    [
        ("ping", {"target": "a.example", "count": 1}),
        ("traceroute", {"target": "a.example"}),
        ("dns_query", {"qname": "a.example"}),
    ],
)
def test_vp_fanout_cap_applies_to_every_measurement(am, patched_vps, big_vp_pool, fn, kwargs):
    """60 matched VPs exceeds the 50-VP cap. This must hold for *every*
    measurement primitive, not just ping — a gap in one is a gap overall."""
    patched_vps(big_vp_pool)
    msg = _err(getattr(am, fn)(**kwargs))
    assert "50" in msg and "60" in msg


def test_fanout_cap_message_is_actionable(am, patched_vps, big_vp_pool):
    """The error must tell the caller how to fix it. An agent that cannot tell
    what to change from the message will retry the same call."""
    patched_vps(big_vp_pool)
    msg = _err(am.ping(target="a.example", count=1)).lower()
    assert "vp_filter" in msg or "narrow" in msg


@pytest.mark.parametrize("count,ok", [(40, True), (41, False)])
def test_total_probe_budget_boundary(am, patched_vps, big_vp_pool, count, ok):
    """50 VPs x 40 probes = 2000, exactly the cap. One more probe each and the
    aggregate budget must reject, even though every individual limit passes."""
    patched_vps(big_vp_pool[:50])
    r = am.ping(target="a.example", count=count)
    assert (r["status"] == "ok") is ok


def test_total_probe_budget_is_independent_of_per_call_limits(am, patched_vps, big_vp_pool):
    """count=120 and 50 VPs are each individually legal; their product is not.
    This is the defence-in-depth check."""
    patched_vps(big_vp_pool[:50])
    assert "6000" in _err(am.ping(target="a.example", count=120))


# --- multi-target and diagnose ---------------------------------------------


@pytest.mark.parametrize("n,ok", [(20, True), (21, False)])
def test_multi_target_cap(am, n, ok):
    targets = [f"h{i}.example" for i in range(n)]
    r = am.ping_multi_targets(targets=targets, vp_filter={"region": "africa"}, count=1)
    assert (r["status"] == "ok") is ok


@pytest.mark.parametrize("n,ok", [(50, True), (51, False)])
def test_diagnose_sample_cap(am, n, ok):
    r = am.diagnose_path_anomaly(
        target="a.example", suspect_asn=7377, vp_filter={"region": "east-asia"}, sample_size=n
    )
    assert (r["status"] == "ok") is ok


# --- limits are not clamps -------------------------------------------------


def test_over_limit_never_returns_partial_data(am, patched_vps, big_vp_pool):
    """A rejected call must carry no data at all. Returning a silently-clamped
    subset would look like a successful measurement of something the caller
    never asked for."""
    patched_vps(big_vp_pool)
    r = am.ping(target="a.example", count=1)
    assert r["status"] == "error" and r["data"] is None
