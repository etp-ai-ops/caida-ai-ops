"""The IP-metadata layer: prefix matching, data-quality guards, and the
hostname rules. No network and no /data mount required — every test builds its
own index, so this runs in CI on a laptop.
"""

from __future__ import annotations

import pytest

from caida_ai_ops import geodata as g

# --- longest-prefix match --------------------------------------------------


def test_longest_prefix_wins():
    """A /30 inside a /24 must win: specificity is the whole point."""
    idx = g.PrefixIndex()
    idx.add("10.0.0.0/8", "broad")
    idx.add("10.1.0.0/16", "narrow")
    idx.add("10.1.2.0/24", "narrowest")
    assert idx.lookup("10.1.2.5") == "narrowest"
    assert idx.lookup("10.1.9.5") == "narrow"
    assert idx.lookup("10.9.9.9") == "broad"
    assert idx.lookup("11.0.0.1") is None


def test_index_handles_v4_and_v6_independently():
    idx = g.PrefixIndex()
    idx.add("192.0.2.0/24", "v4")
    idx.add("2001:db8::/32", "v6")
    assert idx.lookup("192.0.2.1") == "v4"
    assert idx.lookup("2001:db8::1") == "v6"


def test_malformed_prefix_is_ignored_not_fatal():
    idx = g.PrefixIndex()
    idx.add("not-a-prefix", "x")
    idx.add("192.0.2.0/24", "ok")
    assert idx.lookup("192.0.2.1") == "ok"
    assert idx.lookup("garbage") is None


# --- data-quality guards ---------------------------------------------------


@pytest.mark.parametrize(
    "prefix,ok",
    [
        ("192.0.2.0/24", True),
        ("10.0.0.0/12", True),
        ("206.163.25.0/2", False),  # real 2026-08 geofeed row claiming a /2 is Minneapolis
        ("44.0.0.0/9", False),
        ("2001:db8::/32", True),
        ("2001::/16", False),
    ],
)
def test_implausibly_broad_geofeed_prefixes_are_rejected(prefix, ok):
    """One operator typo declaring a city for a quarter of the IPv4 internet
    would otherwise mislocate a billion addresses."""
    assert g._plausible_geofeed_prefix(prefix) is ok


@pytest.mark.parametrize(
    "ip,scope",
    [
        ("10.1.62.49", "private"),
        ("192.168.1.1", "private"),
        ("127.0.0.1", "loopback"),
        ("169.254.1.1", "link-local"),
        ("224.0.0.1", "multicast"),
        ("not-an-ip", "invalid"),
        ("8.8.8.8", None),
        ("2606:4700::1111", None),
    ],
)
def test_non_global_addresses_are_flagged_not_geolocated(ip, scope):
    """A hop at 10.1.62.49 is inside somebody's network, not in a city. Without
    this guard a stray prefix match reports a real-looking location for it."""
    assert g.address_scope(ip) == scope


def test_geolocation_refuses_non_global_addresses():
    """Guard must sit in the lookup itself, not only in callers."""
    assert g.geofeed_for_ip("10.1.62.49") is None
    assert g.country_for_ip("127.0.0.1") is None


# --- hostname inference ----------------------------------------------------


@pytest.fixture
def fake_hoiho(monkeypatch):
    import re

    rules = [
        {
            "domain": "example.net",
            "regexes": [re.compile(r"^[a-z\d\-]+\.([a-z]{3})-agg\d+\.example\.net$")],
            "hints": {"lax": {"city": "Los Angeles", "region": "CA", "country": "US", "code_type": "iata"}},
            "quality": "good",
            "ppv": "0.93",
        }
    ]
    monkeypatch.setitem(g._cache, "hoiho", (rules, {"origin": "test"}))
    return rules


def test_hostname_yields_city_with_its_method(fake_hoiho):
    out = g.city_from_hostname("xe-0-0-0.lax-agg1.example.net")
    assert out["city"] == "Los Angeles"
    # The method matters as much as the answer: an inference must never be
    # indistinguishable from a registry fact downstream.
    assert out["method"] == "hoiho:example.net"
    assert out["code"] == "lax" and out["rule_quality"] == "good"


def test_unmatched_hostname_returns_none_rather_than_guessing(fake_hoiho):
    assert g.city_from_hostname("router1.unknown-isp.com") is None  # no rule
    assert g.city_from_hostname("xe-0.zzz-agg1.example.net") is None  # no geohint
    assert g.city_from_hostname("") is None


def test_hostname_matching_is_domain_anchored(fake_hoiho):
    """`evil-example.net` must not match rules learned for `example.net`."""
    assert g.city_from_hostname("xe-0-0-0.lax-agg1.evil-example.net") is None
