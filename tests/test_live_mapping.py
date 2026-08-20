"""Tests for the live-backend translation layer — the code that turns
scamper's objects into this toolkit's dicts.

None of these tests touch the mux. They use a fake ScamperVp/result objects,
which is the point: this layer is where the live backend's bugs live (it was
entirely broken before, in ways demo mode could never reveal), so it needs
coverage that runs anywhere, including CI with no CAIDA access.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest


def _fake_vp(**over):
    """Stand-in for scamper.ScamperVp: an *object* with attributes, not a dict.
    Confusing the two is precisely the bug this layer exists to prevent."""
    base = dict(
        shortname="hnd-jp",
        name="hnd-jp.ark.caida.org",
        asn4=2914,
        cc="JP",
        st=None,
        place="Tokyo",
        iata="HND",
        ipv4="203.0.113.7",
        loc=(35.6762, 139.6503),
        tags={"network:ipv4", "network:ipv6", "primitive:dns", "os:linux"},
    )
    base.update(over)
    return SimpleNamespace(**base)


# --- VP mapping ------------------------------------------------------------


def test_vp_to_dict_maps_every_consumed_field(am):
    d = am._vp_to_dict(_fake_vp())
    assert d["vp_id"] == "hnd-jp"  # shortname, not name
    assert d["hostname"] == "hnd-jp.ark.caida.org"
    assert d["asn"] == 2914  # asn4 -> asn
    assert d["country"] == "JP"  # cc -> country
    assert d["region"] == "east-asia"  # derived
    assert (d["lat"], d["lon"]) == (35.6762, 139.6503)  # loc tuple -> pair
    assert d["ipv4"] is True and d["ipv6"] is True  # from tags, not addr
    assert d["status"] == "active"


def test_vp_to_dict_reports_no_org_rather_than_inventing_one(am):
    """The mux has no operator name. `None` is the honest answer; a guessed
    org string would be indistinguishable from real data downstream."""
    assert am._vp_to_dict(_fake_vp())["org"] is None


def test_ipv4_flag_comes_from_tags_not_from_the_address(am):
    """ScamperVp.ipv4 is an address string, which is always truthy. Deriving
    the boolean from it would mark every VP IPv4-capable."""
    vp = _fake_vp(tags={"network:ipv6"}, ipv4="203.0.113.7")
    d = am._vp_to_dict(vp)
    assert d["ipv4"] is False and d["ipv6"] is True
    assert d["ipv4_addr"] == "203.0.113.7"


def test_vp_to_dict_survives_missing_location(am):
    d = am._vp_to_dict(_fake_vp(loc=None))
    assert d["lat"] is None and d["lon"] is None


def test_vp_to_dict_handles_no_tags(am):
    d = am._vp_to_dict(_fake_vp(tags=None))
    assert d["tags"] == [] and d["ipv4"] is False


# --- region derivation -----------------------------------------------------


@pytest.mark.parametrize(
    "cc,region",
    [
        ("US", "north-america"),
        ("JP", "east-asia"),
        ("SG", "south-east-asia"),
        ("IN", "south-asia"),
        ("DE", "europe"),
        ("ZA", "africa"),
        ("BR", "south-america"),
        ("AU", "oceania"),
        ("AE", "middle-east"),
    ],
)
def test_region_mapping(am, cc, region):
    assert am.cc_to_region(cc) == region


def test_region_is_case_insensitive(am):
    assert am.cc_to_region("jp") == am.cc_to_region("JP") == "east-asia"


def test_unknown_country_degrades_instead_of_raising(am):
    """A new Ark VP in an unmapped country must still list — and still match
    country/asn/tag filters — rather than vanishing or crashing discovery."""
    assert am.cc_to_region("ZZ") == "unknown"
    assert am.cc_to_region(None) == "unknown"


# --- liveness --------------------------------------------------------------


def test_live_vp_without_heartbeat_is_active(am):
    """The mux only lists attached VPs, so presence implies liveness. Without
    this, every live VP would be filtered out as stale."""
    now = datetime.now(UTC)
    assert am._vp_is_active({"status": "active"}, now) is True


def test_demo_vp_uses_heartbeat_staleness(am):
    now = datetime.now(UTC)
    fresh = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (now - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert am._vp_is_active({"last_heartbeat": fresh}, now) is True
    assert am._vp_is_active({"last_heartbeat": stale}, now) is False


def test_malformed_heartbeat_is_treated_as_inactive(am):
    assert am._vp_is_active({"last_heartbeat": "not-a-date"}, datetime.now(UTC)) is False


# --- result parsing --------------------------------------------------------


def test_parse_ping_computes_loss_and_converts_rtt_to_ms(am):
    obj = SimpleNamespace(
        dst="8.8.8.8",
        probe_count=4,
        nreplies=3,
        min_rtt=timedelta(milliseconds=1.5),
        avg_rtt=timedelta(milliseconds=2.0),
        max_rtt=timedelta(milliseconds=3.0),
        stddev_rtt=timedelta(milliseconds=0.5),
    )
    rec = am._parse_ping(obj)
    assert rec["sent"] == 4 and rec["received"] == 3
    assert rec["loss_pct"] == 25.0
    assert rec["rtt_avg_ms"] == 2.0  # timedelta -> ms, not seconds


def test_parse_ping_total_loss_yields_null_rtts_not_zeros(am):
    """Zero RTT would read as an impossibly fast reply; null means 'no reply'."""
    obj = SimpleNamespace(
        dst="192.0.2.1", probe_count=2, nreplies=0, min_rtt=None, avg_rtt=None, max_rtt=None, stddev_rtt=None
    )
    rec = am._parse_ping(obj)
    assert rec["loss_pct"] == 100.0
    assert rec["rtt_min_ms"] is None and rec["rtt_avg_ms"] is None


def test_parse_ping_zero_probes_does_not_divide_by_zero(am):
    obj = SimpleNamespace(
        dst="a", probe_count=0, nreplies=0, min_rtt=None, avg_rtt=None, max_rtt=None, stddev_rtt=None
    )
    assert am._parse_ping(obj)["loss_pct"] == 0.0


def test_parse_trace_skips_silent_hops_without_renumbering(am):
    """scamper yields None for a TTL that drew no reply. Those must not become
    hops, and the surviving hops must keep their true ttl values — renumbering
    would silently shorten the path and misrepresent the topology."""

    def hop(ttl):
        return SimpleNamespace(
            probe_ttl=ttl,
            src=f"10.0.0.{ttl}",
            name=None,
            rtt=timedelta(milliseconds=ttl),
        )

    obj = SimpleNamespace(
        dst="8.8.8.8",
        hops=lambda: iter([hop(1), hop(2), None, hop(4)]),
        is_stop_completed=lambda: True,
    )
    rec = am._parse_trace(obj)
    assert [h["ttl"] for h in rec["hops"]] == [1, 2, 4]  # 3 absent, 4 not renamed
    assert rec["reached"] is True


def test_parse_trace_captures_hostname_and_defers_asn(am):
    """A raw traceroute records the hop's PTR hostname (the geolocation signal)
    but leaves asn unset — that is filled in later by enrich_result, so the
    measurement never depends on a dataset being present."""
    obj = SimpleNamespace(
        dst="8.8.8.8",
        hops=lambda: iter(
            [
                SimpleNamespace(
                    probe_ttl=1, src="10.0.0.1", name="lax-agg1.example.net", rtt=timedelta(milliseconds=1)
                )
            ]
        ),
        is_stop_completed=lambda: False,
    )
    rec = am._parse_trace(obj)
    assert rec["hops"][0]["hostname"] == "lax-agg1.example.net"
    assert rec["hops"][0]["asn"] is None
    assert rec["reached"] is False


def test_parse_dns_renders_address_and_non_address_records(am):
    a_rr = SimpleNamespace(addr="93.184.216.34")
    ns_rr = SimpleNamespace(addr=None, cname=None, ns="ns1.example.com")
    obj = SimpleNamespace(rcode="NoError", rtt=timedelta(milliseconds=12), ans=lambda: iter([a_rr, ns_rr]))
    rec = am._parse_dns(obj, resolver="system")
    assert rec["answers"] == ["93.184.216.34", "ns1.example.com"]
    assert rec["rcode"] == "NoError" and rec["rtt_ms"] == 12.0
    assert rec["resolver_used"] == "system"


def test_ms_conversion(am):
    assert am._ms(None) is None
    assert am._ms(timedelta(seconds=1)) == 1000.0
    assert am._ms(timedelta(microseconds=1500)) == 1.5
