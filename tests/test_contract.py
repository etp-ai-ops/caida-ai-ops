"""The output contract and VP filtering — the parts every consumer (CLI,
notebook, MCP client) depends on being stable."""

from __future__ import annotations

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
