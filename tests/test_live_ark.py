"""Integration tests against the real Ark mux.

Deselected by default (`addopts = -m 'not live'`). These send real packets from
real vantage points, so they are opt-in:

    pytest -m live

They skip cleanly rather than failing where there is no mux — CI without CAIDA
access is an expected environment, not a broken one. Every probe here is
deliberately tiny: one country, <=2 attempts, low hop limits.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

MUX = os.environ.get("MATTHEWPP_MUX", "/run/ark/mux")


@pytest.fixture(scope="module")
def live(monkeypatch_session=None):
    """Turn demo mode off for this module, and skip if Ark is unreachable."""
    if not Path(MUX).exists():
        pytest.skip(f"no Ark mux at {MUX}")
    prior = os.environ.pop("MATTHEWPP_DEMO", None)
    import importlib

    from caida_ai_ops import ark

    importlib.reload(ark)
    try:
        probe = ark.list_vps(limit=1)
        if probe["status"] != "ok":
            pytest.skip(f"mux present but unusable: {probe.get('error')}")
        yield ark
    finally:
        if prior is not None:
            os.environ["MATTHEWPP_DEMO"] = prior
        importlib.reload(ark)


def test_vp_discovery_returns_a_real_fleet(live):
    vps = live.list_vps()["data"]
    assert len(vps) > 50, "expected a substantial live fleet"
    assert all(v["vp_id"] and v["country"] for v in vps)


def test_no_live_vp_falls_through_the_region_map(live):
    """An unmapped country would make region filters silently lossy."""
    unmapped = sorted({v["country"] for v in live.list_vps()["data"] if v["region"] == "unknown"})
    assert not unmapped, f"country codes missing from _CC_TO_REGION: {unmapped}"


def test_ping_returns_one_record_per_requested_vp(live):
    """Responses arrive out of order and some VPs may not answer; the result
    must still line up 1:1 with the VPs that were asked."""
    vps = live.list_vps(country="JP")["data"]
    r = live.ping(target="1.1.1.1", vp_filter={"country": "JP"}, count=2)
    assert r["status"] == "ok"
    assert len(r["data"]) == len(vps)
    assert {rec["vp_id"] for rec in r["data"]} == {v["vp_id"] for v in vps}


def test_ping_reports_rtts(live):
    recs = live.ping(target="1.1.1.1", vp_filter={"country": "JP"}, count=2)["data"]
    replied = [r for r in recs if r.get("received")]
    assert replied, "no VP answered 1.1.1.1 — suspicious"
    assert all(r["rtt_avg_ms"] > 0 for r in replied)


def test_traceroute_returns_a_path_and_warns_about_null_asns(live):
    r = live.traceroute(target="1.1.1.1", vp_filter={"country": "KR"}, max_hops=6)
    assert r["status"] == "ok"
    trace = r["data"]["traces"][0]
    assert trace["hops"], "expected at least one responding hop"
    assert all(h["asn"] is None for h in trace["hops"])
    assert any("asn" in w for w in r["warnings"]), "the null-ASN caveat must be surfaced"


def test_dns_query_resolves(live):
    recs = live.dns_query(
        qname="example.com", qtype="A", vp_filter={"country": "DE", "tag": "primitive:dns"}
    )["data"]
    answered = [r for r in recs if r.get("answers")]
    assert answered, "no VP resolved example.com"


def test_unfiltered_measurement_is_refused_against_the_real_fleet(live):
    """The most important live assertion: the fan-out cap actually stands
    between an unfiltered request and the entire fleet."""
    r = live.ping(target="1.1.1.1", count=1)
    assert r["status"] == "error"
    assert "50" in r["error"]["message"]
