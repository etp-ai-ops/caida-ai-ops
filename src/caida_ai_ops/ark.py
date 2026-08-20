"""ark_measurement — active Ark measurement functions for Matthew++.

Vantage-point discovery, ping, traceroute, DNS, and multi-VP root-cause
diagnosis — the capability layer of the toolkit, built on ``matthewpp_core``.

Every active-probing function (`ping`, `traceroute`, `dns_query`, and
anything that calls them) enforces hard limits — see "Hard limits" below —
so an agent driving this toolkit cannot accidentally (or on request) launch
an overload against Ark's shared vantage points or the target it's probing.
These limits are not configurable at call time; narrow your `vp_filter` or
make repeated smaller calls instead of raising them.

Two backends, selected automatically:

- **Live backend** (default): lazily imports CAIDA's ``scamper`` package
  (``ScamperCtrl``) and connects to the Ark mux socket. Requires real CAIDA
  credentials/network access (see ``etp-2026-overview/scamper-orientation.md``
  for how to get access); if unavailable, functions fail loudly with
  `ArkMuxUnavailableError` — never silently.

  Live dispatch is queue-based, matching the real scamper API:
  ``ctrl.add_vps(...)`` -> ``ctrl.instances()`` -> queue one measurement per
  instance -> drain ``ctrl.responses(timeout=...)``. See `_live_measure`,
  which is the single place that loop lives; `ping`, `traceroute` and
  `dns_query` each supply only a "what to ask" and a "how to read it".

  Two live-mode caveats worth knowing before trusting a result:

  - ``org`` is ``None`` on live VPs — the mux reports ``asn`` but no
    operator name.
  - traceroute hops carry ``asn: None`` — the mux returns hop IPs with no
    IP-to-ASN mapping, so the AS-matching helpers (`check_transit`,
    `list_hops_by_asn`, `diagnose_path_anomaly`) cannot match on live data.
    Live `traceroute` attaches an explicit warning saying so.
- **Demo backend** (``MATTHEWPP_DEMO=1``): swaps in a small, fixed set of
  synthetic VPs so every function here can be exercised — and the CLI
  demoed — with no CAIDA infrastructure at all. Every demo-mode response
  carries an explicit warning; nothing here pretends synthetic data is real.

Output contract: every public function returns
``{"status", "function", "parameters", "data", "warnings", "provenance"}``
(see ``matthewpp_core.envelope``/``Result``).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import statistics
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .core import (
    MatthewPPError,
    Result,
    demo_warning,
    envelope,
    haversine_km,
    is_demo_mode,
    now_iso,
    results_dir,
    write_output,
)
from .geodata import (
    GeoDataUnavailableError,
    address_scope,
    asn_for_ip,
    city_coordinates,
    city_from_hostname,
    country_for_ip,
    geofeed_for_ip,
    prefixes_in_city,
    rtt_consistency,
    source_versions,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ArkMuxUnavailableError(MatthewPPError):
    """The Ark mux socket / ``scamper`` package could not be reached."""


class NoMatchingVPsError(MatthewPPError):
    """A vantage-point filter matched zero active Ark VPs."""


class InvalidFilterError(MatthewPPError):
    """A filter argument (country code, coordinate pair, ...) was malformed."""


class UnsupportedMethodError(MatthewPPError):
    """An unsupported ping/traceroute method was requested."""


class UnsupportedQtypeError(MatthewPPError):
    """An unsupported DNS record type was requested."""


class UnknownRunIdError(MatthewPPError):
    """A referenced traceroute ``run_id`` was not found in the run store."""


class UnknownResultIdError(MatthewPPError):
    """A referenced ``result_id`` was not found in the result store."""


class UnsupportedFormatError(MatthewPPError):
    """An unsupported export format was requested."""


class MeasurementLimitExceededError(MatthewPPError):
    """A requested measurement exceeds one of this module's hard limits —
    see the "Hard limits" constants below. Not overridable at call time;
    narrow the request instead."""


# ---------------------------------------------------------------------------
# Hard limits — enforced unconditionally, to keep an agent (or a human) from
# overloading Ark's shared vantage points or a probed target.
# ---------------------------------------------------------------------------

MAX_PING_DURATION_S = 60.0
MAX_PING_COUNT = 120
MIN_PING_INTERVAL_MS = 200
MIN_PING_TIMEOUT_MS = 100
MAX_PING_TIMEOUT_MS = 10_000
MAX_TOTAL_PING_PROBES = 2_000  # vp_count * probe_count, defense in depth

MAX_TRACEROUTE_HOPS = 64
MAX_TRACEROUTE_ATTEMPTS_PER_HOP = 5
MIN_TRACEROUTE_WAIT_MS = 100
MAX_TRACEROUTE_WAIT_MS = 5_000

MIN_DNS_TIMEOUT_MS = 100
MAX_DNS_TIMEOUT_MS = 10_000

MAX_VPS_PER_MEASUREMENT = 50  # single-call VP fan-out cap for ping/traceroute/dns
MAX_MULTI_TARGETS = 20  # ping_multi_targets target-list cap
MAX_DIAGNOSE_SAMPLE_SIZE = 50


def _require_range(value, lo, hi, name: str) -> None:
    if value is not None and not (lo <= value <= hi):
        raise MeasurementLimitExceededError(f"{name} must be between {lo} and {hi} (got {value})")


def _enforce_vp_fanout_limit(vps: list[dict], fn_name: str) -> None:
    if len(vps) > MAX_VPS_PER_MEASUREMENT:
        raise MeasurementLimitExceededError(
            f"{fn_name}: {len(vps)} VPs matched, which exceeds the hard cap of "
            f"{MAX_VPS_PER_MEASUREMENT} VPs per call. Narrow vp_filter (region/country/"
            f"asn/tag) or split the work across multiple smaller calls."
        )


# ---------------------------------------------------------------------------
# Demo fixtures (used only when MATTHEWPP_DEMO is truthy)
# ---------------------------------------------------------------------------

_DEMO_VPS: list[dict] = [
    {
        "vp_id": "ark-jnb-za",
        "hostname": "jnb-za.ark.caida.org",
        "asn": 36937,
        "org": "MTN SA",
        "country": "ZA",
        "region": "africa",
        "lat": -26.2041,
        "lon": 28.0473,
        "ipv4": True,
        "ipv6": True,
        "tags": ["residential"],
        "last_heartbeat": now_iso(),
    },
    {
        "vp_id": "ark-lag-ng",
        "hostname": "lag-ng.ark.caida.org",
        "asn": 37146,
        "org": "MainOne",
        "country": "NG",
        "region": "africa",
        "lat": 6.5244,
        "lon": 3.3792,
        "ipv4": True,
        "ipv6": False,
        "tags": [],
        "last_heartbeat": now_iso(),
    },
    {
        "vp_id": "ark-fra-de",
        "hostname": "fra-de.ark.caida.org",
        "asn": 3320,
        "org": "Deutsche Telekom",
        "country": "DE",
        "region": "europe",
        "lat": 50.1109,
        "lon": 8.6821,
        "ipv4": True,
        "ipv6": True,
        "tags": ["cloud"],
        "last_heartbeat": now_iso(),
    },
    {
        "vp_id": "ark-lon-gb",
        "hostname": "lon-gb.ark.caida.org",
        "asn": 2856,
        "org": "BT",
        "country": "GB",
        "region": "europe",
        "lat": 51.5072,
        "lon": -0.1276,
        "ipv4": True,
        "ipv6": True,
        "tags": [],
        "last_heartbeat": now_iso(),
    },
    {
        "vp_id": "ark-nrt-jp",
        "hostname": "nrt-jp.ark.caida.org",
        "asn": 2914,
        "org": "NTT",
        "country": "JP",
        "region": "east-asia",
        "lat": 35.6762,
        "lon": 139.6503,
        "ipv4": True,
        "ipv6": True,
        "tags": ["cloud"],
        "last_heartbeat": now_iso(),
    },
    {
        "vp_id": "ark-sin-sg",
        "hostname": "sin-sg.ark.caida.org",
        "asn": 4657,
        "org": "StarHub",
        "country": "SG",
        "region": "east-asia",
        "lat": 1.3521,
        "lon": 103.8198,
        "ipv4": True,
        "ipv6": False,
        "tags": [],
        "last_heartbeat": now_iso(),
    },
    {
        "vp_id": "ark-gru-br",
        "hostname": "gru-br.ark.caida.org",
        "asn": 28573,
        "org": "Vivo",
        "country": "BR",
        "region": "south-america",
        "lat": -23.5505,
        "lon": -46.6333,
        "ipv4": True,
        "ipv6": True,
        "tags": [],
        "last_heartbeat": now_iso(),
    },
    {
        "vp_id": "ark-sea-us",
        "hostname": "sea-us.ark.caida.org",
        "asn": 209,
        "org": "CenturyLink",
        "country": "US",
        "region": "north-america",
        "lat": 47.6062,
        "lon": -122.3321,
        "ipv4": True,
        "ipv6": True,
        "tags": ["cloud"],
        "last_heartbeat": now_iso(),
    },
    {
        "vp_id": "ark-sdc-us",
        "hostname": "sdc-us.ark.caida.org",
        "asn": 7377,
        "org": "UC San Diego",
        "country": "US",
        "region": "north-america",
        "lat": 32.8801,
        "lon": -117.2340,
        "ipv4": True,
        "ipv6": True,
        "tags": ["caida"],
        "last_heartbeat": now_iso(),
    },
    {
        "vp_id": "ark-syd-au",
        "hostname": "syd-au.ark.caida.org",
        "asn": 1221,
        "org": "Telstra",
        "country": "AU",
        "region": "oceania",
        "lat": -33.8688,
        "lon": 151.2093,
        "ipv4": True,
        "ipv6": True,
        "tags": [],
        "last_heartbeat": now_iso(),
    },
]


def _demo_warning() -> list[str]:
    return demo_warning("Ark")


# ---------------------------------------------------------------------------
# Backend accessor — lazy import, fail loudly, demo-mode short-circuit.
# ---------------------------------------------------------------------------


def _get_scamper_ctrl():
    """Return a live ``ScamperCtrl`` instance, or raise.

    Not called at all in demo mode — callers branch on ``is_demo_mode()``
    before reaching this.
    """
    try:
        from scamper import ScamperCtrl  # type: ignore  # CAIDA-internal package
    except ImportError as exc:
        raise ArkMuxUnavailableError(
            "the 'scamper' package (ScamperCtrl) is not installed in this environment. "
            "Set MATTHEWPP_DEMO=1 to exercise this function against synthetic fixtures, "
            "or run inside CAIDA's Ark-enabled environment (see "
            "etp-2026-overview/scamper-orientation.md for access)."
        ) from exc
    try:
        return ScamperCtrl(mux=os.environ.get("MATTHEWPP_MUX", "/run/ark/mux"))
    except Exception as exc:  # noqa: BLE001
        raise ArkMuxUnavailableError(f"could not connect to the Ark mux socket: {exc}") from exc


def _dispatch(fn: Callable[[Any], Any], items: list, parallel: bool, max_workers: int = 16) -> list:
    """Apply ``fn`` to each of ``items``, concurrently if ``parallel``,
    sequentially otherwise, preserving input order either way."""
    if not parallel or len(items) <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        return list(pool.map(fn, items))


# ---------------------------------------------------------------------------
# Shared vantage-point filtering (pure logic; used by every Ark-facing fn)
# ---------------------------------------------------------------------------

_STALE_AFTER = timedelta(hours=1)


# ---------------------------------------------------------------------------
# Live VP mapping — scamper's ScamperVp -> this module's VP dict schema.
#
# ScamperVp is an object (attributes: name, shortname, asn4, cc, st, place,
# iata, ipv4, loc, tags), not a dict, and its field names differ from the
# schema every function here consumes. _vp_to_dict is the single translation
# point between the two.
# ---------------------------------------------------------------------------

# Region names match the demo fixtures' vocabulary, so `region` filters behave
# identically against live and synthetic VPs. Covers every country code the
# Ark fleet currently reports, plus common others.
_CC_TO_REGION: dict[str, str] = {
    # africa
    "GH": "africa",
    "GM": "africa",
    "KE": "africa",
    "MG": "africa",
    "MU": "africa",
    "NG": "africa",
    "RW": "africa",
    "TZ": "africa",
    "UG": "africa",
    "ZA": "africa",
    "EG": "africa",
    "MA": "africa",
    # europe
    "AT": "europe",
    "BA": "europe",
    "BE": "europe",
    "BG": "europe",
    "CH": "europe",
    "CY": "europe",
    "CZ": "europe",
    "DE": "europe",
    "ES": "europe",
    "FI": "europe",
    "FR": "europe",
    "GR": "europe",
    "IE": "europe",
    "IS": "europe",
    "IT": "europe",
    "LT": "europe",
    "LU": "europe",
    "LV": "europe",
    "NL": "europe",
    "NO": "europe",
    "PL": "europe",
    "PT": "europe",
    "RO": "europe",
    "RS": "europe",
    "RU": "europe",
    "SE": "europe",
    "UA": "europe",
    "UK": "europe",
    "GB": "europe",
    "DK": "europe",
    "EE": "europe",
    "HR": "europe",
    "HU": "europe",
    "SI": "europe",
    "SK": "europe",
    "MD": "europe",
    # east-asia
    "CN": "east-asia",
    "HK": "east-asia",
    "JP": "east-asia",
    "KR": "east-asia",
    "MN": "east-asia",
    "TW": "east-asia",
    # south-east-asia
    "ID": "south-east-asia",
    "MY": "south-east-asia",
    "PH": "south-east-asia",
    "SG": "south-east-asia",
    "TH": "south-east-asia",
    "VN": "south-east-asia",
    # south-asia
    "BD": "south-asia",
    "BT": "south-asia",
    "IN": "south-asia",
    "NP": "south-asia",
    "LK": "south-asia",
    "PK": "south-asia",
    # middle-east
    "AE": "middle-east",
    "IL": "middle-east",
    "QA": "middle-east",
    "SA": "middle-east",
    "TR": "middle-east",
    "JO": "middle-east",
    # north-america
    "CA": "north-america",
    "CR": "north-america",
    "GU": "north-america",
    "MX": "north-america",
    "US": "north-america",
    "VI": "north-america",
    "PA": "north-america",
    "GT": "north-america",
    # south-america
    "AR": "south-america",
    "BR": "south-america",
    "CL": "south-america",
    "CO": "south-america",
    "EC": "south-america",
    "PY": "south-america",
    "PE": "south-america",
    "UY": "south-america",
    "VE": "south-america",
    "BO": "south-america",
    # oceania
    "AU": "oceania",
    "NZ": "oceania",
    "FJ": "oceania",
    "PG": "oceania",
}


def cc_to_region(cc: str | None) -> str:
    """Map an ISO country code to this module's region vocabulary.

    Unknown codes map to ``"unknown"`` rather than raising, so a new Ark VP
    in an unmapped country still lists (and still matches country/ASN/tag
    filters) instead of disappearing from every result.
    """
    return _CC_TO_REGION.get((cc or "").upper(), "unknown")


def _vp_to_dict(vp: Any) -> dict:
    """Translate one live ``ScamperVp`` into this module's VP dict schema.

    Fields with no live equivalent are set to ``None`` rather than invented:
    ``org`` (the mux reports no operator name — only ``asn``).

    ``status`` is always ``"active"``: the mux lists only VPs currently
    attached to it, so presence in ``ctrl.vps()`` *is* liveness. There is no
    ``last_heartbeat`` to report, and none is fabricated — see `_filter_vps`
    for how ``active_only`` handles each case.
    """
    loc = getattr(vp, "loc", None)
    tags = sorted(vp.tags or [])
    cc = (vp.cc or "").upper()
    return {
        "vp_id": vp.shortname,
        "hostname": vp.name,
        "asn": vp.asn4,
        "org": None,
        "country": cc,
        "region": cc_to_region(cc),
        "lat": loc[0] if loc else None,
        "lon": loc[1] if loc else None,
        "ipv4": "network:ipv4" in tags,
        "ipv6": "network:ipv6" in tags,
        "tags": tags,
        "status": "active",
        "place": getattr(vp, "place", None),
        "iata": getattr(vp, "iata", None),
        "ipv4_addr": str(vp.ipv4) if getattr(vp, "ipv4", None) else None,
    }


def _fetch_raw_vps() -> list[dict]:
    if is_demo_mode():
        return _DEMO_VPS
    ctrl = _get_scamper_ctrl()
    return [_vp_to_dict(v) for v in ctrl.vps()]


def _vp_is_active(vp: dict, now: datetime) -> bool:
    """Is this VP currently usable?

    Two liveness conventions, because the two backends genuinely differ:

    - ``last_heartbeat`` present (demo fixtures): active if the heartbeat is
      newer than ``_STALE_AFTER``.
    - no ``last_heartbeat`` (live mux): the mux only reports VPs currently
      attached to it, so presence already implies liveness. Fall back to the
      ``status`` field rather than fabricating a timestamp.
    """
    hb = vp.get("last_heartbeat")
    if hb is None:
        return vp.get("status", "active") == "active"
    try:
        hb_dt = datetime.strptime(hb, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return False
    return now - hb_dt <= _STALE_AFTER


def _filter_vps(
    vps: list[dict],
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    near_lat_lon: tuple[float, float] | None = None,
    radius_km: float = 500.0,
    ipv6_only: bool = False,
    active_only: bool = True,
) -> list[dict]:
    if near_lat_lon is not None:
        if len(near_lat_lon) != 2:
            raise InvalidFilterError("near_lat_lon must be a (lat, lon) pair")
        lat0, lon0 = near_lat_lon
    out = []
    now = datetime.now(UTC)
    for vp in vps:
        if country is not None and vp.get("country", "").upper() != country.upper():
            continue
        if region is not None and region.lower() not in vp.get("region", "").lower():
            continue
        if asn is not None and vp.get("asn") != asn:
            continue
        if tag is not None and tag not in (vp.get("tags") or []):
            continue
        if vp_ids is not None and vp.get("vp_id") not in vp_ids:
            continue
        if ipv6_only and not vp.get("ipv6"):
            continue
        if active_only and not _vp_is_active(vp, now):
            continue
        if near_lat_lon is not None:
            if vp.get("lat") is None or vp.get("lon") is None:
                continue
            dist = haversine_km(lat0, lon0, vp["lat"], vp["lon"])
            vp = {**vp, "_distance_km": dist}
            if dist > radius_km:
                continue
        out.append(vp)
    if near_lat_lon is not None:
        out.sort(key=lambda v: v.get("_distance_km", float("inf")))
    return out


# ---------------------------------------------------------------------------
# Category 1 — Vantage point discovery
# ---------------------------------------------------------------------------


@envelope("matthewpp.ark.vps.list_vps")
def list_vps(
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    near_lat_lon: tuple[float, float] | None = None,
    radius_km: float = 500.0,
    ipv6_only: bool = False,
    active_only: bool = True,
    limit: int | None = None,
) -> Result:
    """List Ark vantage points (VPs) matching the given filters.

    Args:
        region: Free-text macro-region filter, e.g. "africa", "east-asia".
            Matched as a case-insensitive substring against each VP's region.
        country: ISO 3166-1 alpha-2 country code, e.g. "ZA", "JP".
        asn: Filter to VPs whose hosting network is this AS number.
        tag: Require this exact tag (e.g. "ipv6", "cloud", "residential").
        vp_ids: Restrict to this explicit allowlist of VP IDs.
        near_lat_lon: ``(lat, lon)`` in decimal degrees; if set, only VPs
            within ``radius_km`` are returned, sorted nearest-first.
        radius_km: Radius in km used with ``near_lat_lon``. Default 500.0.
        ipv6_only: If True, only return VPs with IPv6 connectivity.
        active_only: If True (default), exclude VPs whose last heartbeat is
            older than one hour.
        limit: Cap the number of VPs returned. ``None`` = no cap. (This is
            a result-size cap, not a probing cap — plain VP-metadata lookups
            aren't subject to this module's active-measurement hard limits.)

    Returns:
        ``data`` is a list of VP records: ``{vp_id, hostname, asn, org,
        country, region, lat, lon, ipv4, ipv6, tags, last_heartbeat}``
        (plus ``_distance_km`` per VP if ``near_lat_lon`` was given).

    Raises:
        ArkMuxUnavailableError: the Ark mux socket/`scamper` package is
            unreachable (live mode only).
        InvalidFilterError: ``near_lat_lon`` was malformed.
        NoMatchingVPsError: the filters matched zero active VPs.
    """
    vps = _fetch_raw_vps()
    filtered = _filter_vps(
        vps,
        region=region,
        country=country,
        asn=asn,
        tag=tag,
        vp_ids=vp_ids,
        near_lat_lon=near_lat_lon,
        radius_km=radius_km,
        ipv6_only=ipv6_only,
        active_only=active_only,
    )
    if limit is not None:
        filtered = filtered[:limit]
    if not filtered:
        raise NoMatchingVPsError("no active Ark VPs matched the given filters")
    warnings = _demo_warning() if is_demo_mode() else []
    return Result(data=filtered, warnings=warnings, provenance_extra={"vp_count_used": len(filtered)})


@envelope("matthewpp.ark.vps.get_vp_by_id")
def get_vp_by_id(vp_id: str) -> Result:
    """Fetch a single Ark VP's metadata by its VP ID.

    Args:
        vp_id: The Ark VP identifier, e.g. ``"ark-jnb-za"``.

    Returns:
        ``data`` is the single VP record (same shape as `list_vps`).

    Raises:
        NoMatchingVPsError: no VP with this ID exists (or is active).
    """
    vps = _fetch_raw_vps()
    matches = [v for v in vps if v.get("vp_id") == vp_id]
    if not matches:
        raise NoMatchingVPsError(f"no VP found with vp_id={vp_id!r}")
    warnings = _demo_warning() if is_demo_mode() else []
    return Result(data=matches[0], warnings=warnings)


@envelope("matthewpp.ark.vps.count_vps")
def count_vps(
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    active_only: bool = True,
) -> Result:
    """Count Ark VPs matching the given filters, without fetching full records.

    Args:
        region: See `list_vps`.
        country: See `list_vps`.
        asn: See `list_vps`.
        tag: See `list_vps`.
        active_only: See `list_vps`.

    Returns:
        ``data`` is ``{"count": int}``. Unlike `list_vps`, a zero match is
        not an error here — a count of zero is itself a valid answer.
    """
    vps = _fetch_raw_vps()
    filtered = _filter_vps(vps, region=region, country=country, asn=asn, tag=tag, active_only=active_only)
    warnings = _demo_warning() if is_demo_mode() else []
    return Result(data={"count": len(filtered)}, warnings=warnings)


@envelope("matthewpp.ark.vps.nearest_vps")
def nearest_vps(lat: float, lon: float, n: int = 5, active_only: bool = True) -> Result:
    """Find the ``n`` Ark VPs geographically nearest to a point.

    Args:
        lat: Latitude of the reference point, decimal degrees.
        lon: Longitude of the reference point, decimal degrees.
        n: Number of nearest VPs to return. Default 5.
        active_only: See `list_vps`.

    Returns:
        ``data`` is a list of up to ``n`` VP records, nearest-first, each
        with a ``_distance_km`` field.

    Raises:
        NoMatchingVPsError: no active, geolocated VPs exist at all.
    """
    vps = _fetch_raw_vps()
    filtered = _filter_vps(vps, near_lat_lon=(lat, lon), radius_km=float("inf"), active_only=active_only)
    if not filtered:
        raise NoMatchingVPsError("no active, geolocated Ark VPs available")
    warnings = _demo_warning() if is_demo_mode() else []
    return Result(data=filtered[:n], warnings=warnings)


# ---------------------------------------------------------------------------
# Category 2 — Active reachability & latency (ping)
# ---------------------------------------------------------------------------

_SUPPORTED_PING_METHODS = {"icmp-echo", "icmp-time", "tcp-syn", "udp"}


# ---------------------------------------------------------------------------
# Live measurement dispatch.
#
# The scamper mux API is queue-based, not call-per-VP: attach the VPs you want
# (`add_vps`), queue one measurement per attached instance, signal `done()`,
# then drain `responses()` as results arrive out of order. `_live_measure`
# is the single place that loop lives; ping/traceroute/dns_query each supply
# a `queue_fn` (what to ask each VP) and a `parse_fn` (how to read one result
# object), and get back records in the same order as the VPs they passed in.
# ---------------------------------------------------------------------------


def _ms(td: Any) -> float | None:
    """timedelta (scamper's RTT unit) -> milliseconds, rounded."""
    if td is None:
        return None
    return round(td.total_seconds() * 1000.0, 3)


def _live_measure(
    vps: list[dict],
    queue_fn: Callable[[Any, Any], None],
    parse_fn: Callable[[Any], dict],
    timeout_s: float,
    fn_name: str,
) -> list[dict]:
    """Run one measurement per VP over the mux and collect the results.

    Args:
        vps: VP dicts (as returned by `list_vps`) to measure from.
        queue_fn: called as ``queue_fn(ctrl, inst)`` to queue the measurement
            for one attached instance.
        parse_fn: called as ``parse_fn(obj)`` to turn one scamper result
            object into this module's per-VP record.
        timeout_s: wall-clock budget for draining every response.
        fn_name: caller name, for error messages.

    Returns:
        One record per input VP, in input order. A VP that returned nothing
        within ``timeout_s`` (or whose measurement errored) still gets a
        record, carrying an ``error`` key — a silent gap in a fan-out result
        set is indistinguishable from a network finding, so it is never left
        to inference.
    """
    ctrl = _get_scamper_ctrl()
    wanted = {vp["vp_id"] for vp in vps}
    objs = [v for v in ctrl.vps() if v.shortname in wanted]
    found = {v.shortname for v in objs}
    if not objs:
        raise NoMatchingVPsError(
            f"{fn_name}: none of the {len(wanted)} requested VPs are currently "
            f"attached to the Ark mux. VP availability changes continuously; "
            f"re-run list_vps to see what is live now."
        )

    ctrl.add_vps(objs)
    for inst in ctrl.instances():
        queue_fn(ctrl, inst)
    ctrl.done()

    records: dict[str, dict] = {}
    errors: list[str] = []
    try:
        for obj in ctrl.responses(timeout=timedelta(seconds=timeout_s)):
            inst = getattr(obj, "inst", None)
            vp_id = getattr(inst, "shortname", None)
            try:
                rec = parse_fn(obj)
            except Exception as exc:  # noqa: BLE001 - one bad result must not sink the batch
                rec = {"error": f"could not parse result: {type(exc).__name__}: {exc}"}
            rec["vp_id"] = vp_id
            records[vp_id] = rec
    finally:
        for exc in ctrl.exceptions():
            errors.append(str(exc))

    out = []
    for vp in vps:
        vp_id = vp["vp_id"]
        if vp_id in records:
            out.append(records[vp_id])
        elif vp_id not in found:
            out.append({"vp_id": vp_id, "error": "VP was not attached to the mux at dispatch time"})
        else:
            out.append({"vp_id": vp_id, "error": f"no result returned within {timeout_s:.0f}s"})
    return out


def _parse_ping(obj: Any) -> dict:
    sent = obj.probe_count or 0
    recv = obj.nreplies or 0
    return {
        "target_ip": str(obj.dst),
        "sent": sent,
        "received": recv,
        "loss_pct": round(100.0 * (sent - recv) / sent, 1) if sent else 0.0,
        "rtt_min_ms": _ms(obj.min_rtt),
        "rtt_avg_ms": _ms(obj.avg_rtt),
        "rtt_max_ms": _ms(obj.max_rtt),
        "rtt_stddev_ms": _ms(obj.stddev_rtt),
    }


def _parse_trace(obj: Any) -> dict:
    hops = []
    for hop in obj.hops():
        # hops() yields None for a TTL that drew no reply; keep the ttl slot
        # visible rather than silently renumbering the path.
        if hop is None:
            continue
        hops.append(
            {
                "ttl": hop.probe_ttl,
                "ip": str(hop.src) if hop.src else None,
                # PTR hostname, requested via ptr=True. Router names frequently
                # encode a city ("losa4", "lax"), which enrich_result decodes with
                # CAIDA's hoiho rules -- so this is a geolocation signal, not just
                # a nicety. About 70% of hops have one.
                "hostname": hop.name,
                "asn": None,  # filled in by enrich_result from routeviews-prefix2as
                "rtt_ms": _ms(hop.rtt),
            }
        )
    return {
        "target_ip": str(obj.dst),
        "hops": hops,
        "reached": bool(obj.is_stop_completed()),
    }


def _rr_value(rr: Any) -> str:
    """Render one DNS resource record as a string, whatever its type."""
    for attr in ("addr", "cname", "ns", "ptr", "txt", "mx", "soa", "svcb", "https"):
        val = getattr(rr, attr, None)
        if val is not None:
            return str(val)
    return str(rr)


def _parse_dns(obj: Any, resolver: str) -> dict:
    answers = [_rr_value(rr) for rr in obj.ans()]
    return {
        "resolver_used": resolver,
        "answers": answers,
        "rcode": str(obj.rcode),
        "rtt_ms": _ms(obj.rtt),
    }


def _demo_ping_one(vp: dict, target: str, count: int) -> dict:
    base = (abs(hash((vp["vp_id"], target))) % 180) + 5.0
    jitter = [round(base + (i % 3) * 1.5, 2) for i in range(max(count, 1))]
    received = max(count - (1 if "unreachable-demo" in target else 0), 0)
    return {
        "vp_id": vp["vp_id"],
        "target_ip": target,
        "sent": count,
        "received": received,
        "loss_pct": round(100.0 * (count - received) / count, 1) if count else 0.0,
        "rtt_min_ms": min(jitter),
        "rtt_avg_ms": round(sum(jitter) / len(jitter), 2),
        "rtt_max_ms": max(jitter),
        "rtt_stddev_ms": round(statistics.pstdev(jitter), 2) if len(jitter) > 1 else 0.0,
    }


@envelope("matthewpp.ark.ping.ping", persist=True)
def ping(
    target: str,
    vp_filter: dict | None = None,
    duration_s: float | None = 10.0,
    count: int | None = None,
    interval_ms: int = 1000,
    timeout_ms: int = 2000,
    method: str = "icmp-echo",
    parallel: bool = True,
) -> Result:
    """Ping a target from one or more Ark vantage points.

    Hard limits (see module docstring): ``duration_s`` <= 60s, ``count`` <=
    120, ``interval_ms`` >= 200ms, ``timeout_ms`` in [100, 10000]ms, at most
    50 VPs per call, and total probes (VPs x count) <= 2000. Violating any
    of these raises `MeasurementLimitExceededError` rather than clamping —
    narrow the request instead of retrying with adjusted values silently.

    Args:
        target: Hostname or IP address to ping. Resolved once centrally
            (not independently per VP) in the live backend.
        vp_filter: Filter dict accepted by `list_vps` (``region``,
            ``country``, ``asn``, ``tag``, ``near_lat_lon``, ``radius_km``,
            ``ipv6_only``). ``None`` = every active Ark VP (a warning is
            attached if unfiltered, since that is expensive).
        duration_s: Wall-clock seconds to probe from each VP. Default 10,
            hard max 60.
        count: Fixed probe count per VP; overrides ``duration_s`` if set.
            Hard max 120.
        interval_ms: Milliseconds between probes from a single VP. Hard
            min 200 (prevents flooding a target).
        timeout_ms: Per-probe reply timeout in milliseconds. Must be in
            [100, 10000].
        method: One of ``"icmp-echo"``, ``"icmp-time"``, ``"tcp-syn"``,
            ``"udp"``.
        parallel: If True (default), dispatch to all matched VPs
            concurrently.

    Returns:
        ``data`` is a list of per-VP results: ``{vp_id, target_ip, sent,
        received, loss_pct, rtt_min_ms, rtt_avg_ms, rtt_max_ms,
        rtt_stddev_ms}``.

    Raises:
        UnsupportedMethodError: ``method`` is not supported.
        MeasurementLimitExceededError: a hard limit above was exceeded.
        ArkMuxUnavailableError, NoMatchingVPsError: as in `list_vps`.
    """
    if method not in _SUPPORTED_PING_METHODS:
        raise UnsupportedMethodError(f"unsupported ping method: {method!r}")
    _require_range(duration_s, 0.001, MAX_PING_DURATION_S, "duration_s")
    _require_range(count, 1, MAX_PING_COUNT, "count")
    if interval_ms < MIN_PING_INTERVAL_MS:
        raise MeasurementLimitExceededError(
            f"interval_ms must be >= {MIN_PING_INTERVAL_MS} (got {interval_ms})"
        )
    _require_range(timeout_ms, MIN_PING_TIMEOUT_MS, MAX_PING_TIMEOUT_MS, "timeout_ms")

    vps = list_vps(**(vp_filter or {}))["data"]
    _enforce_vp_fanout_limit(vps, "ping")
    probe_count = count if count is not None else max(int((duration_s or 10.0) * 1000 / interval_ms), 1)
    if len(vps) * probe_count > MAX_TOTAL_PING_PROBES:
        raise MeasurementLimitExceededError(
            f"{len(vps)} VPs x {probe_count} probes = {len(vps) * probe_count} total probes, "
            f"which exceeds the hard cap of {MAX_TOTAL_PING_PROBES}. Reduce VP count, "
            f"duration_s, or count."
        )

    warnings = list(_demo_warning()) if is_demo_mode() else []
    if vp_filter is None:
        warnings.append("no vp_filter given — probing every active Ark VP; this is expensive and slow.")

    if is_demo_mode():
        probe_one = lambda vp: _demo_ping_one(vp, target, probe_count)  # noqa: E731
        results = _dispatch(probe_one, vps, parallel=parallel)
    else:
        # Budget: every probe's spacing, plus one full timeout for the last
        # reply, plus slack for mux round-trips.
        budget_s = (probe_count * interval_ms + timeout_ms) / 1000.0 + 15.0
        results = _live_measure(
            vps,
            queue_fn=lambda ctrl, inst: ctrl.do_ping(
                target,
                inst=inst,
                attempts=probe_count,
                method=method,
                wait_probe=timedelta(milliseconds=interval_ms),
                wait_timeout=timedelta(milliseconds=timeout_ms),
            ),
            parse_fn=_parse_ping,
            timeout_s=budget_s,
            fn_name="ping",
        )
    return Result(data=results, warnings=warnings, provenance_extra={"vp_count_used": len(vps)})


@envelope("matthewpp.ark.ping.is_reachable", persist=True)
def is_reachable(target: str, vp_filter: dict | None = None, timeout_ms: int = 2000) -> Result:
    """Single-probe reachability check: is ``target`` reachable from the
    matched VPs right now?

    Thin wrapper over `ping` with ``count=1`` (so `ping`'s hard limits apply
    unchanged), for the common "is X up from Y" question where full RTT
    statistics aren't needed.

    Args:
        target: Hostname or IP address to check.
        vp_filter: See `ping`.
        timeout_ms: Per-probe reply timeout in milliseconds.

    Returns:
        ``data`` is ``{"pct_reachable": float, "per_vp": [{"vp_id",
        "reachable": bool}]}``.
    """
    raw = ping(target, vp_filter=vp_filter, count=1, timeout_ms=timeout_ms)
    per_vp = [{"vp_id": r["vp_id"], "reachable": r["received"] > 0} for r in raw["data"]]
    pct = round(100.0 * sum(1 for p in per_vp if p["reachable"]) / len(per_vp), 1) if per_vp else 0.0
    return Result(data={"pct_reachable": pct, "per_vp": per_vp}, warnings=raw["warnings"])


@envelope("matthewpp.ark.ping.ping_multi_targets", persist=True)
def ping_multi_targets(targets: list[str], vp_filter: dict | None = None, **ping_kwargs) -> Result:
    """Ping several targets from the same set of Ark VPs, one target at a
    time, and return results grouped by target.

    Hard limit: at most 20 targets per call (each target incurs a full
    `ping` call against every matched VP, so this is a multiplicative cost
    control on top of `ping`'s own limits).

    Args:
        targets: List of hostnames/IPs to ping. Max 20.
        vp_filter: See `ping`; applied identically to every target so
            results are directly comparable across targets.
        **ping_kwargs: Passed through to `ping` for every target (e.g.
            ``duration_s``, ``method``).

    Returns:
        ``data`` is ``{target: [per-VP ping result, ...], ...}``.

    Raises:
        MeasurementLimitExceededError: more than 20 targets were given.
    """
    if len(targets) > MAX_MULTI_TARGETS:
        raise MeasurementLimitExceededError(
            f"{len(targets)} targets given, which exceeds the hard cap of {MAX_MULTI_TARGETS} "
            f"per ping_multi_targets call."
        )
    out: dict[str, list] = {}
    warnings: list[str] = []
    for t in targets:
        r = ping(t, vp_filter=vp_filter, **ping_kwargs)
        out[t] = r["data"]
        warnings.extend(w for w in r["warnings"] if w not in warnings)
    return Result(data=out, warnings=warnings)


@envelope("matthewpp.ark.ping.compare_ping_regions", persist=True)
def compare_ping_regions(target: str, region_a: str, region_b: str, **ping_kwargs) -> Result:
    """Compare average latency to a target between two regions.

    Args:
        target: Hostname or IP to ping.
        region_a: First region filter (as accepted by `list_vps`'s
            ``region``).
        region_b: Second region filter.
        **ping_kwargs: Passed through to `ping` (e.g. ``duration_s``,
            ``method``) — `ping`'s hard limits apply unchanged.

    Returns:
        ``data`` is ``{"region_a": {"region": ..., "avg_rtt_ms": ...},
        "region_b": {...}, "delta_ms": region_b.avg - region_a.avg}``.
    """
    ra = ping(target, vp_filter={"region": region_a}, **ping_kwargs)
    rb = ping(target, vp_filter={"region": region_b}, **ping_kwargs)
    avg_a = round(statistics.mean(r["rtt_avg_ms"] for r in ra["data"]), 2)
    avg_b = round(statistics.mean(r["rtt_avg_ms"] for r in rb["data"]), 2)
    data = {
        "region_a": {"region": region_a, "avg_rtt_ms": avg_a, "vp_count": len(ra["data"])},
        "region_b": {"region": region_b, "avg_rtt_ms": avg_b, "vp_count": len(rb["data"])},
        "delta_ms": round(avg_b - avg_a, 2),
    }
    return Result(data=data, warnings=ra["warnings"] + rb["warnings"])


# ---------------------------------------------------------------------------
# Category 3 — Path measurement & route-anomaly detection (traceroute)
# ---------------------------------------------------------------------------

_SUPPORTED_TR_METHODS = {"icmp-paris", "udp-paris", "tcp"}


def _run_store_path() -> Path:
    default = (
        Path(os.environ["OUTPUT_DIR"]) / "ark-runs.json"
        if os.environ.get("OUTPUT_DIR")
        else Path.home() / ".caida-ai-ops" / "runs.json"
    )
    return Path(os.environ.get("MATTHEWPP_RUN_STORE", str(default))).expanduser()


def _load_run_store() -> dict:
    path = _run_store_path()
    if not path.exists():
        return {"runs": {}, "baselines": {}}
    with path.open() as fh:
        return json.load(fh)


def _save_run_store(store: dict) -> None:
    path = _run_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        json.dump(store, fh, indent=2, default=str)


def _demo_traceroute_one(vp: dict, target: str) -> dict:
    hops = [
        {"ttl": 1, "ip": "10.0.0.1", "asn": vp["asn"], "rtt_ms": 1.2},
        {"ttl": 2, "ip": f"198.51.100.{(hash(vp['vp_id']) % 200) + 1}", "asn": vp["asn"], "rtt_ms": 4.5},
    ]
    # Demo quirk: any VP in "asia" region routes toward an "example" target
    # through the UC San Diego ASN (7377) — a synthetic stand-in for the
    # real SDSC-detour incident this whole toolkit was built to catch.
    if "asia" in vp["region"] and "example" in target:
        hops.append({"ttl": 3, "ip": "192.0.2.53", "asn": 7377, "rtt_ms": 180.3})
    hops.append({"ttl": len(hops) + 1, "ip": "203.0.113.10", "asn": 15169, "rtt_ms": 32.1})
    return {"vp_id": vp["vp_id"], "target_ip": target, "hops": hops, "reached": True}


@envelope("matthewpp.ark.traceroute.traceroute", persist=True)
def traceroute(
    target: str,
    vp_filter: dict | None = None,
    method: str = "icmp-paris",
    max_hops: int = 32,
    attempts_per_hop: int = 2,
    wait_ms: int = 1000,
) -> Result:
    """Run traceroute(s) to a target from one or more Ark vantage points.

    Hard limits: ``max_hops`` <= 64, ``attempts_per_hop`` <= 5, ``wait_ms``
    in [100, 5000]ms, at most 50 VPs per call.

    Every call is persisted to a local run store (``MATTHEWPP_RUN_STORE``,
    default ``~/.matthewpp/runs.json``) under a fresh ``run_id``, so it can
    be referenced later by `compare_paths`, `get_run`, or
    `save_run_as_baseline`.

    Args:
        target: Hostname or IP address to trace to.
        vp_filter: See `list_vps`. ``None`` = every active VP (expensive).
        method: One of ``"icmp-paris"``, ``"udp-paris"``, ``"tcp"``.
        max_hops: Maximum TTL to probe before giving up. Hard max 64.
        attempts_per_hop: Probes per hop before marking it non-responsive.
            Hard max 5.
        wait_ms: Per-probe reply timeout in milliseconds. Must be in
            [100, 5000].

    Returns:
        ``data`` is ``{"run_id": str, "target": str, "traces": [{vp_id,
        target_ip, hops: [{ttl, ip, asn, rtt_ms}], reached}, ...]}``.

    Raises:
        UnsupportedMethodError: ``method`` is not supported.
        MeasurementLimitExceededError: a hard limit above was exceeded.
        ArkMuxUnavailableError, NoMatchingVPsError: as in `list_vps`.
    """
    if method not in _SUPPORTED_TR_METHODS:
        raise UnsupportedMethodError(f"unsupported traceroute method: {method!r}")
    _require_range(max_hops, 1, MAX_TRACEROUTE_HOPS, "max_hops")
    _require_range(attempts_per_hop, 1, MAX_TRACEROUTE_ATTEMPTS_PER_HOP, "attempts_per_hop")
    _require_range(wait_ms, MIN_TRACEROUTE_WAIT_MS, MAX_TRACEROUTE_WAIT_MS, "wait_ms")

    vps = list_vps(**(vp_filter or {}))["data"]
    _enforce_vp_fanout_limit(vps, "traceroute")

    warnings = _demo_warning() if is_demo_mode() else []
    if is_demo_mode():
        traces = [_demo_traceroute_one(vp, target) for vp in vps]
    else:
        budget_s = (max_hops * attempts_per_hop * wait_ms) / 1000.0 + 20.0
        traces = _live_measure(
            vps,
            queue_fn=lambda ctrl, inst: ctrl.do_trace(
                target,
                inst=inst,
                method=method,
                hoplimit=max_hops,
                attempts=attempts_per_hop,
                ptr=True,
                wait_timeout=timedelta(milliseconds=wait_ms),
            ),
            parse_fn=_parse_trace,
            timeout_s=budget_s,
            fn_name="traceroute",
        )
        warnings.append(
            "per-hop 'asn' and 'geo' are unset until this run is enriched: the mux "
            "returns hop addresses and hostnames only. Call enrich_result(run_id's "
            "result_id) to fill them in from routeviews-prefix2as, geofeeds and hoiho. "
            "Until then, AS-based helpers (check_transit, list_hops_by_asn, "
            "diagnose_path_anomaly) cannot match hops on live data."
        )

    run_id = f"tr_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    # Keep archived runs self-contained so later enrichment can check a
    # location claim without reconnecting to the mux or assuming the VP still
    # exists at the same location.
    vp_locations = {vp["vp_id"]: {"lat": vp.get("lat"), "lon": vp.get("lon")} for vp in vps}
    store = _load_run_store()
    store["runs"][run_id] = {
        "run_id": run_id,
        "target": target,
        "timestamp_utc": now_iso(),
        "traces": traces,
        "vp_locations": vp_locations,
    }
    _save_run_store(store)

    data = {
        "run_id": run_id,
        "target": target,
        "traces": traces,
        "vp_locations": vp_locations,
    }
    return Result(
        data=data, warnings=warnings, provenance_extra={"vp_count_used": len(vps), "run_id": run_id}
    )


@envelope("matthewpp.ark.traceroute.get_run")
def get_run(run_id: str) -> Result:
    """Fetch a previously stored `traceroute` run by its ``run_id``.

    Args:
        run_id: A run ID returned by a prior `traceroute` call, or resolved
            from a baseline label via `save_run_as_baseline`.

    Returns:
        ``data`` is the stored run record: ``{run_id, target,
        timestamp_utc, traces}``.

    Raises:
        UnknownRunIdError: no run (and no baseline label) matches ``run_id``.
    """
    store = _load_run_store()
    if run_id in store["baselines"]:
        run_id = store["baselines"][run_id]
    if run_id not in store["runs"]:
        raise UnknownRunIdError(f"no stored run or baseline found for {run_id!r}")
    return Result(data=store["runs"][run_id])


@envelope("matthewpp.ark.traceroute.list_runs")
def list_runs(target: str | None = None, vp_id: str | None = None, limit: int = 20) -> Result:
    """List recent stored traceroute runs, optionally filtered.

    Args:
        target: Only return runs to this target.
        vp_id: Only return runs that included this VP.
        limit: Max number of runs to return, most recent first.

    Returns:
        ``data`` is a list of ``{run_id, target, timestamp_utc,
        vp_count}`` summaries (not full hop data — use `get_run` for that).
    """
    store = _load_run_store()
    runs = list(store["runs"].values())
    if target is not None:
        runs = [r for r in runs if r["target"] == target]
    if vp_id is not None:
        runs = [r for r in runs if any(t["vp_id"] == vp_id for t in r["traces"])]
    runs.sort(key=lambda r: r["timestamp_utc"], reverse=True)
    summaries = [
        {
            "run_id": r["run_id"],
            "target": r["target"],
            "timestamp_utc": r["timestamp_utc"],
            "vp_count": len(r["traces"]),
        }
        for r in runs[:limit]
    ]
    return Result(data=summaries)


@envelope("matthewpp.ark.traceroute.save_run_as_baseline")
def save_run_as_baseline(run_id: str, label: str) -> Result:
    """Give a stored run a human-friendly label so later calls can say
    ``baseline_run_id="last-tuesday"`` instead of the raw run ID.

    Args:
        run_id: An existing run ID (from `traceroute` or `list_runs`).
        label: A short label to register, e.g. ``"last-tuesday"`` or
            ``"pre-incident"``.

    Returns:
        ``data`` is ``{"label": label, "run_id": run_id}``.

    Raises:
        UnknownRunIdError: ``run_id`` does not exist.
    """
    store = _load_run_store()
    if run_id not in store["runs"]:
        raise UnknownRunIdError(f"no stored run found for {run_id!r}")
    store["baselines"][label] = run_id
    _save_run_store(store)
    return Result(data={"label": label, "run_id": run_id})


def _extract_hop_ips(trace: dict) -> set[str]:
    return {h["ip"] for h in trace.get("hops", [])}


@envelope("matthewpp.ark.traceroute.compare_paths", persist=True)
def compare_paths(
    target: str,
    vp_filter: dict | None = None,
    baseline_run_id: str | None = None,
    flag_transit_asn: int | None = None,
    flag_transit_ip_prefix: str | None = None,
) -> Result:
    """Run a fresh traceroute and compare it against a prior run and/or flag
    transit through a suspect AS/prefix — the direct tool for "is traffic
    detouring through X?" questions.

    Args:
        target: Hostname or IP address to trace to.
        vp_filter: See `list_vps`.
        baseline_run_id: A run ID (or label registered via
            `save_run_as_baseline`) to diff the fresh trace against. If
            ``None``, only the flag checks run (no historical diff).
        flag_transit_asn: Flag any current path whose hops include this AS.
        flag_transit_ip_prefix: Flag any current path with a hop IP inside
            this CIDR prefix.

    Returns:
        ``data`` is ``{"run_id": new_run_id, "per_vp": [{"vp_id",
        "path_changed", "hops_added": [ip,...], "hops_removed": [ip,...],
        "transits_flagged_asn": bool, "transits_flagged_prefix": bool}]}``.

    Raises:
        UnknownRunIdError: ``baseline_run_id`` does not resolve to a stored
            run.
        ArkMuxUnavailableError, NoMatchingVPsError: as in `list_vps`.
    """
    fresh = traceroute(target, vp_filter=vp_filter)
    baseline = get_run(baseline_run_id) if baseline_run_id else None
    baseline_by_vp = {t["vp_id"]: t for t in baseline["data"]["traces"]} if baseline else {}
    prefix = ipaddress.ip_network(flag_transit_ip_prefix) if flag_transit_ip_prefix else None

    per_vp = []
    for trace in fresh["data"]["traces"]:
        cur_ips = _extract_hop_ips(trace)
        base_trace = baseline_by_vp.get(trace["vp_id"])
        if base_trace:
            base_ips = _extract_hop_ips(base_trace)
            added, removed = sorted(cur_ips - base_ips), sorted(base_ips - cur_ips)
            path_changed = bool(added or removed)
        else:
            added, removed, path_changed = [], [], (baseline_run_id is not None)

        transits_asn = flag_transit_asn is not None and any(
            h.get("asn") == flag_transit_asn for h in trace["hops"]
        )
        transits_prefix = False
        if prefix is not None:
            for h in trace["hops"]:
                try:
                    if ipaddress.ip_address(h["ip"]) in prefix:
                        transits_prefix = True
                        break
                except ValueError:
                    continue

        per_vp.append(
            {
                "vp_id": trace["vp_id"],
                "path_changed": path_changed,
                "hops_added": added,
                "hops_removed": removed,
                "transits_flagged_asn": transits_asn,
                "transits_flagged_prefix": transits_prefix,
            }
        )

    return Result(
        data={"run_id": fresh["data"]["run_id"], "per_vp": per_vp},
        warnings=fresh["warnings"],
        provenance_extra={"run_id": fresh["data"]["run_id"], "baseline_run_id": baseline_run_id},
    )


@envelope("matthewpp.ark.traceroute.check_transit", persist=True)
def check_transit(
    target: str, asn: int | None = None, ip_prefix: str | None = None, vp_filter: dict | None = None
) -> Result:
    """One-shot version of `compare_paths` for the common case: no
    historical baseline, just "does the current path go through X?"

    Args:
        target: Hostname or IP to trace to.
        asn: AS number to check for transit.
        ip_prefix: CIDR prefix to check for transit.
        vp_filter: See `list_vps`.

    Returns:
        ``data`` is ``{"pct_vps_transiting": float, "transiting_vp_ids":
        [...], "run_id": str}``.
    """
    r = compare_paths(target, vp_filter=vp_filter, flag_transit_asn=asn, flag_transit_ip_prefix=ip_prefix)
    per_vp = r["data"]["per_vp"]
    transiting = [v["vp_id"] for v in per_vp if v["transits_flagged_asn"] or v["transits_flagged_prefix"]]
    pct = round(100.0 * len(transiting) / len(per_vp), 1) if per_vp else 0.0
    return Result(
        data={"pct_vps_transiting": pct, "transiting_vp_ids": transiting, "run_id": r["data"]["run_id"]},
        warnings=r["warnings"],
    )


@envelope("matthewpp.ark.traceroute.list_hops_by_asn")
def list_hops_by_asn(run_id: str, asn: int) -> Result:
    """Within a stored traceroute run, list every hop that belongs to a
    given AS number, across all VPs in that run.

    Args:
        run_id: A stored run ID (or baseline label).
        asn: AS number to filter hops by.

    Returns:
        ``data`` is a list of ``{vp_id, ttl, ip, rtt_ms}`` for every
        matching hop.

    Raises:
        UnknownRunIdError: ``run_id`` does not resolve to a stored run.
    """
    run = get_run(run_id)["data"]
    matches = []
    for trace in run["traces"]:
        for hop in trace["hops"]:
            if hop.get("asn") == asn:
                matches.append(
                    {"vp_id": trace["vp_id"], "ttl": hop["ttl"], "ip": hop["ip"], "rtt_ms": hop["rtt_ms"]}
                )
    return Result(data=matches)


# ---------------------------------------------------------------------------
# Category 4 — DNS divergence measurement
# ---------------------------------------------------------------------------

_SUPPORTED_QTYPES = {"A", "AAAA", "NS", "MX", "TXT", "DS", "DNSKEY", "CAA", "SOA"}


def _demo_dns_one(vp: dict, qname: str, qtype: str) -> dict:
    if qtype in ("DS", "DNSKEY"):
        answers = [] if "nodnssec" in qname else [f"{qtype.lower()}-record-for-{qname}"]
    else:
        answers = ["198.51.100.9"] if vp["region"] != "oceania" else ["198.51.100.99"]
    return {
        "vp_id": vp["vp_id"],
        "resolver_used": "system",
        "answers": answers,
        "rcode": "NOERROR",
        "rtt_ms": 25.0,
    }


@envelope("matthewpp.ark.dns.dns_query", persist=True)
def dns_query(
    qname: str,
    qtype: str = "A",
    vp_filter: dict | None = None,
    resolver: str = "system",
    timeout_ms: int = 2000,
) -> Result:
    """Issue a DNS query from one or more Ark vantage points.

    Hard limits: ``timeout_ms`` in [100, 10000]ms, at most 50 VPs per call.

    Args:
        qname: Domain name to query.
        qtype: One of ``A``, ``AAAA``, ``NS``, ``MX``, ``TXT``, ``DS``,
            ``DNSKEY``, ``CAA``, ``SOA``.
        vp_filter: See `list_vps`.
        resolver: ``"system"`` (each VP's local resolver, default — best
            for detecting real geo/anycast divergence) or a specific
            resolver IP to query uniformly from every VP instead.
        timeout_ms: Per-query reply timeout in milliseconds.

    Returns:
        ``data`` is a list of per-VP answers: ``{vp_id, resolver_used,
        answers, rcode, rtt_ms}``.

    Raises:
        UnsupportedQtypeError: ``qtype`` is not supported.
        MeasurementLimitExceededError: a hard limit above was exceeded.
        ArkMuxUnavailableError, NoMatchingVPsError: as in `list_vps`.
    """
    if qtype not in _SUPPORTED_QTYPES:
        raise UnsupportedQtypeError(f"unsupported DNS record type: {qtype!r}")
    _require_range(timeout_ms, MIN_DNS_TIMEOUT_MS, MAX_DNS_TIMEOUT_MS, "timeout_ms")
    vps = list_vps(**(vp_filter or {}))["data"]
    _enforce_vp_fanout_limit(vps, "dns_query")
    if is_demo_mode():
        results = [_demo_dns_one(vp, qname, qtype) for vp in vps]
    else:
        # resolver="system" means "the VP's own resolver" -> pass no server.
        server = None if resolver == "system" else resolver
        results = _live_measure(
            vps,
            queue_fn=lambda ctrl, inst: ctrl.do_dns(
                qname,
                inst=inst,
                qtype=qtype,
                server=server,
                wait_timeout=timedelta(milliseconds=timeout_ms),
            ),
            parse_fn=lambda obj: _parse_dns(obj, resolver),
            timeout_s=timeout_ms / 1000.0 + 20.0,
            fn_name="dns_query",
        )
    warnings = _demo_warning() if is_demo_mode() else []
    return Result(data=results, warnings=warnings, provenance_extra={"vp_count_used": len(vps)})


@envelope("matthewpp.ark.dns.dns_divergence_report", persist=True)
def dns_divergence_report(
    qname: str, qtype: str = "A", vp_filter: dict | None = None, resolver: str = "system"
) -> Result:
    """Group `dns_query` answers by distinct answer set, to directly answer
    "does the answer for this domain differ depending on where you ask
    from?" without the caller writing any grouping logic.

    Args:
        qname: Domain name to query.
        qtype: See `dns_query`.
        vp_filter: See `list_vps`.
        resolver: See `dns_query`.

    Returns:
        ``data`` is ``{"unique_answer_sets": int, "majority_answer": [...],
        "majority_vp_ids": [...], "minority_vps": [{"vp_id", "answers"}],
        "groups": [{"answers": [...], "vp_ids": [...]}]}``.
    """
    raw = dns_query(qname, qtype=qtype, vp_filter=vp_filter, resolver=resolver)
    groups: dict[tuple, list[str]] = {}
    for r in raw["data"]:
        key = tuple(sorted(r["answers"]))
        groups.setdefault(key, []).append(r["vp_id"])
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    majority_key, majority_vps = ordered[0] if ordered else ((), [])
    minority_vps = [{"vp_id": vp_id, "answers": list(key)} for key, vps in ordered[1:] for vp_id in vps]
    data = {
        "unique_answer_sets": len(groups),
        "majority_answer": list(majority_key),
        "majority_vp_ids": majority_vps,
        "minority_vps": minority_vps,
        "groups": [{"answers": list(k), "vp_ids": v} for k, v in ordered],
    }
    return Result(data=data, warnings=raw["warnings"])


@envelope("matthewpp.ark.dns.check_dnssec_valid", persist=True)
def check_dnssec_valid(qname: str, vp_filter: dict | None = None) -> Result:
    """Simplified per-VP DNSSEC presence check: does a domain return both a
    DS record (from the parent) and a DNSKEY record (from itself)?

    This is a presence heuristic, not a full chain-of-trust cryptographic
    validation — see ``docs/MEASUREMENT_SPEC.md`` §9 for a
    fuller DNSSEC validator.

    Args:
        qname: Domain name to check.
        vp_filter: See `list_vps`.

    Returns:
        ``data`` is a list of ``{vp_id, has_ds, has_dnskey, likely_secured}``.
    """
    ds = dns_query(qname, qtype="DS", vp_filter=vp_filter)
    dnskey = dns_query(qname, qtype="DNSKEY", vp_filter=vp_filter)
    dnskey_by_vp = {r["vp_id"]: r for r in dnskey["data"]}
    out = []
    for r in ds["data"]:
        has_ds = bool(r["answers"])
        dk = dnskey_by_vp.get(r["vp_id"], {"answers": []})
        has_dnskey = bool(dk["answers"])
        out.append(
            {
                "vp_id": r["vp_id"],
                "has_ds": has_ds,
                "has_dnskey": has_dnskey,
                "likely_secured": has_ds and has_dnskey,
            }
        )
    return Result(data=out, warnings=ds["warnings"])


# ---------------------------------------------------------------------------
# Category 5 — Multi-vantage-point root-cause diagnosis
# ---------------------------------------------------------------------------


@envelope("matthewpp.ark.diagnose.bgp_context_for_asn")
def bgp_context_for_asn(asn: int) -> Result:
    """Roadmap stub: BGP cross-reference for a suspect AS.

    Not implemented yet — the real BGP module (see Roadmap in
    ``docs/MEASUREMENT_SPEC.md``) is what `diagnose_path_anomaly`
    calls here once built. Kept as its own function (rather than silently
    inlined) so it has one obvious place to wire in real RouteViews-backed
    logic later, without changing `diagnose_path_anomaly`'s call site.

    Args:
        asn: AS number to fetch BGP context for.

    Returns:
        ``data`` is always ``{"status": "unavailable", "asn": asn,
        "reason": "BGP module not yet implemented"}`` in the current build.
    """
    return Result(data={"status": "unavailable", "asn": asn, "reason": "BGP module not yet implemented"})


@envelope("matthewpp.ark.diagnose.diagnose_path_anomaly", persist=True)
def diagnose_path_anomaly(
    target: str,
    suspect_asn: int | None = None,
    suspect_ip_prefix: str | None = None,
    vp_filter: dict | None = None,
    sample_size: int = 15,
    include_bgp_context: bool = True,
) -> Result:
    """Run a scoped multi-VP diagnostic for a suspected routing anomaly.

    Hard limit: ``sample_size`` <= 50 (this module's general VP-fan-out
    cap), enforced by `traceroute`/`compare_paths` underneath.

    Orchestrates `list_vps` (to pick a sample), `compare_paths` (to check
    transit through the suspect AS/prefix across that sample), and
    optionally `bgp_context_for_asn`. Deliberately returns evidence, not a
    verdict — the caller (agent or human) draws the conclusion.

    Args:
        target: Hostname or IP suspected of anomalous routing.
        suspect_asn: AS number to check for unexpected transit.
        suspect_ip_prefix: CIDR prefix to check for unexpected transit.
        vp_filter: Restrict the diagnostic sample (e.g.
            ``{"region": "southeast-asia"}``); ``None`` = sample broadly.
        sample_size: VP sample size when `vp_filter` doesn't already narrow
            the pool. Applied via `list_vps`'s ``limit``. Hard max 50.
        include_bgp_context: Whether to attempt the (currently stubbed)
            BGP cross-reference step.

    Returns:
        ``data`` is ``{"pct_vps_affected": float, "affected_regions":
        [...], "affected_vp_ids": [...], "bgp_context": {...}}``.

    Raises:
        MeasurementLimitExceededError: ``sample_size`` exceeds 50, or the
            resolved VP set does.
        ArkMuxUnavailableError, NoMatchingVPsError: as in `list_vps`.
    """
    _require_range(sample_size, 1, MAX_DIAGNOSE_SAMPLE_SIZE, "sample_size")
    sample_filter = dict(vp_filter or {})
    sample_filter.setdefault("limit", sample_size)
    vps = list_vps(**sample_filter)["data"]

    cmp = compare_paths(
        target,
        vp_filter={"vp_ids": [v["vp_id"] for v in vps]},
        flag_transit_asn=suspect_asn,
        flag_transit_ip_prefix=suspect_ip_prefix,
    )
    vp_by_id = {v["vp_id"]: v for v in vps}
    affected = [
        row for row in cmp["data"]["per_vp"] if row["transits_flagged_asn"] or row["transits_flagged_prefix"]
    ]
    affected_regions = sorted(
        {vp_by_id[row["vp_id"]]["region"] for row in affected if row["vp_id"] in vp_by_id}
    )
    pct = round(100.0 * len(affected) / len(cmp["data"]["per_vp"]), 1) if cmp["data"]["per_vp"] else 0.0

    bgp_context = (
        bgp_context_for_asn(suspect_asn)["data"] if (include_bgp_context and suspect_asn) else "unavailable"
    )

    data = {
        "pct_vps_affected": pct,
        "affected_regions": affected_regions,
        "affected_vp_ids": [row["vp_id"] for row in affected],
        "bgp_context": bgp_context,
        "run_id": cmp["data"]["run_id"],
    }
    return Result(data=data, warnings=cmp["warnings"])


# ---------------------------------------------------------------------------
# Category 6 — the result store
#
# Every measurement that costs packets is archived by `matthewpp_core`. These
# functions read that archive back: without them the store would be write-only
# from an agent's point of view, and results on disk it cannot find are results
# that may as well not exist.
# ---------------------------------------------------------------------------


def _read_index() -> list[dict]:
    path = results_dir() / "index.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn final line must not make the whole store unreadable
    return out


@envelope("matthewpp.ark.results.list_results")
def list_results(
    function: str | None = None,
    target: str | None = None,
    backend: str | None = None,
    limit: int = 20,
) -> Result:
    """List archived measurement results, most recent first.

    Args:
        function: Substring match on the function name (e.g. ``"ping"``).
        target: Substring match on any target the measurement was pointed at.
        backend: ``"live"`` or ``"demo"`` — filter by which backend produced it.
        limit: Maximum entries to return (default 20).

    Returns:
        ``data`` is a list of ``{result_id, saved_at_utc, function, backend,
        status, targets, vp_count, path}`` — the index only. Use
        `get_result` for a full record.
    """
    rows = _read_index()
    if function:
        rows = [r for r in rows if function.lower() in r.get("function", "").lower()]
    if target:
        rows = [r for r in rows if any(target.lower() in t.lower() for t in r.get("targets", []))]
    if backend:
        rows = [r for r in rows if r.get("backend") == backend.lower()]
    rows.reverse()  # index is append-order; newest first is what callers want
    return Result(
        data=rows[:limit],
        provenance_extra={"results_dir": str(results_dir()), "total_matched": len(rows)},
    )


@envelope("matthewpp.ark.results.get_result")
def get_result(result_id: str) -> Result:
    """Fetch one archived result in full, by ``result_id``.

    Args:
        result_id: As returned by `list_results` (e.g.
            ``"20260819T213524Z_041f2c"``).

    Returns:
        ``data`` is the complete stored record: schema version, metadata
        (function, backend, targets, VP ids, parameters), status, warnings,
        provenance, and the measurement data itself.

    Raises:
        UnknownResultIdError: no such result in the store.
    """
    for row in _read_index():
        if row.get("result_id") == result_id:
            path = results_dir() / row["path"]
            if not path.exists():
                raise UnknownResultIdError(
                    f"result {result_id!r} is in the index but its file is missing: {path}"
                )
            return Result(data=json.loads(path.read_text()))
    raise UnknownResultIdError(f"no result with id {result_id!r}; use list_results to see what exists")


@envelope("matthewpp.ark.results.export_result")
def export_result(result_id: str, output_format: str = "csv", output_file: str | None = None) -> Result:
    """Export one archived result as CSV, JSON, or Markdown.

    The per-VP records of a measurement are inherently tabular, and CSV is what
    a spreadsheet or pandas actually wants. This writes a file next to the
    stored result unless ``output_file`` says otherwise.

    Args:
        result_id: As returned by `list_results`.
        output_format: ``"csv"``, ``"json"``, or ``"md"``.
        output_file: Destination path. Defaults to the stored result's path
            with the format's extension.

    Returns:
        ``data`` is ``{"path": str, "rows": int, "format": str}``.

    Raises:
        UnknownResultIdError: no such result.
        UnsupportedFormatError: unrecognised ``output_format``.
    """
    if output_format not in {"csv", "json", "md"}:
        raise UnsupportedFormatError(f"unsupported format {output_format!r}; use one of: csv, json, md")
    record = get_result(result_id)["data"]
    data = record.get("data")

    # Flatten to rows. Per-VP lists are already tabular; the nested traceroute
    # shape becomes one row per hop so a path is analysable in a spreadsheet.
    rows: list[dict] = []
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict) and isinstance(data.get("traces"), list):
        for trace in data["traces"]:
            for hop in trace.get("hops", []):
                rows.append(
                    {
                        "vp_id": trace.get("vp_id"),
                        "target_ip": trace.get("target_ip"),
                        "reached": trace.get("reached"),
                        **hop,
                    }
                )
    elif isinstance(data, dict) and isinstance(data.get("per_vp"), list):
        rows = [r for r in data["per_vp"] if isinstance(r, dict)]
    elif isinstance(data, dict):
        rows = [data]

    if output_file is None:
        stored = results_dir() / next(r["path"] for r in _read_index() if r["result_id"] == result_id)
        output_file = str(stored.with_suffix("." + output_format))

    # write_output consumes an envelope and serializes its "data" key, so the
    # flattened rows are handed over in that shape; json exports the whole
    # stored record instead, metadata included.
    payload = record if output_format == "json" else {"data": rows}
    write_output(payload, path=output_file, output_format=output_format)
    return Result(
        data={"path": output_file, "rows": len(rows), "format": output_format},
        provenance_extra={"result_id": result_id},
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_vp_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--region")
    p.add_argument("--country")
    p.add_argument("--asn", type=int)
    p.add_argument("--tag")
    p.add_argument("--near", help='"lat,lon" center point for a radius filter')
    p.add_argument("--radius-km", type=float, default=500.0)
    p.add_argument("--ipv6-only", action="store_true")
    p.add_argument("--active-only", dest="active_only", action="store_true", default=True)
    p.add_argument("--include-inactive", dest="active_only", action="store_false")


def _vp_filter_from_args(args: argparse.Namespace) -> dict:
    near = None
    if getattr(args, "near", None):
        lat_s, lon_s = args.near.split(",")
        near = (float(lat_s), float(lon_s))
    return {
        "region": args.region,
        "country": args.country,
        "asn": args.asn,
        "tag": args.tag,
        "near_lat_lon": near,
        "radius_km": args.radius_km,
        "ipv6_only": args.ipv6_only,
        "active_only": args.active_only,
    }


# ---------------------------------------------------------------------------
# Category 7 — IP metadata and enrichment
#
# Measurement and annotation are deliberately separate. Probing costs packets
# and cannot be repeated cheaply; annotation is free, reproducible, and depends
# on datasets that change under you. So measurements are stored raw and
# enriched afterwards -- see `enrich_result`.
# ---------------------------------------------------------------------------


@envelope("matthewpp.ark.geo.find_ips_in_city")
def find_ips_in_city(
    city: str, country: str | None = None, limit: int = 10, candidates_per_prefix: int = 1
) -> Result:
    """Find candidate IP addresses located in a city, for use as probe targets.

    Sourced from RFC 8805 geofeeds: prefixes whose *operator* declares their
    location. That is a self-declaration, not a measurement -- it can be stale
    or wrong. Probe the candidates before trusting them; `is_reachable` is the
    cheap way, and many hosts drop ICMP regardless of whether the location is
    right.

    Args:
        city: City name, e.g. ``"Los Angeles"`` (case-insensitive).
        country: ISO country code to disambiguate, e.g. ``"US"``. Strongly
            recommended -- city names repeat across countries.
        limit: Maximum candidate addresses to return.
        candidates_per_prefix: Addresses to take from each matching prefix.

    Returns:
        ``data`` is ``{"city", "country", "prefix_count", "candidates":
        [{"ip", "prefix", "city", "country", "source"}]}``.

    Raises:
        GeoDataUnavailableError: geofeed data is not available here.
        NoMatchingVPsError: no prefixes are declared for that city.
    """
    prefixes = prefixes_in_city(city, country)
    if not prefixes:
        raise NoMatchingVPsError(
            f"no geofeed prefixes declared for {city!r}"
            + (f" in {country!r}" if country else "")
            + ". Check spelling, or try without a country filter."
        )
    candidates = []
    for prefix in prefixes:
        if len(candidates) >= limit:
            break
        try:
            net = ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            continue
        for i, host in enumerate(net.hosts()):
            if i >= candidates_per_prefix or len(candidates) >= limit:
                break
            candidates.append(
                {
                    "ip": str(host),
                    "prefix": prefix,
                    "city": city,
                    "country": country,
                    "source": "rfc8805-geofeed",
                }
            )
    return Result(
        data={"city": city, "country": country, "prefix_count": len(prefixes), "candidates": candidates},
        warnings=[
            "geofeeds are operator self-declarations, not measurements: verify "
            "reachability (and plausibility) before treating a candidate as located there"
        ],
    )


@envelope("matthewpp.ark.geo.lookup_ip")
def lookup_ip(ip: str) -> Result:
    """Look up everything known about one IP: ASN, city, and country.

    Each field carries the source that produced it, because the sources differ
    enormously in authority: a registry allocation is fact, an operator geofeed
    is a claim, and a hostname inference is a guess.

    Args:
        ip: The IPv4 or IPv6 address to look up.

    Returns:
        ``data`` is ``{"ip", "asn", "geofeed", "registry", "sources"}``. Any
        field is ``None`` when no dataset covers that address -- which is
        common and is not an error.
    """
    out: dict[str, Any] = {"ip": ip, "asn": None, "geofeed": None, "registry": None}
    notes = []
    for key, fn, label in (
        ("asn", asn_for_ip, "routeviews-prefix2as"),
        ("geofeed", geofeed_for_ip, "rfc8805-geofeed"),
        ("registry", country_for_ip, "rir-delegated"),
    ):
        try:
            out[key] = fn(ip)
        except GeoDataUnavailableError as exc:
            notes.append(f"{label} unavailable: {exc}")
    return Result(data=out, warnings=notes, provenance_extra={"sources": source_versions()})


def _annotate_hop(hop: dict, vp_loc: dict | None = None) -> dict:
    """Add ASN and geo data, optionally checking the geo claim against RTT."""
    ip = hop.get("ip")
    enriched = dict(hop)
    if not ip:
        return enriched
    scope = address_scope(ip)
    if scope:
        # Private/reserved hops are real and worth showing, but they have no
        # location. Say which, rather than leaving a null that reads as
        # "lookup failed".
        enriched["geo"] = {"scope": scope, "method": "address-scope", "confidence": "not-geolocatable"}
        return enriched
    try:
        enriched["asn"] = asn_for_ip(ip)
    except GeoDataUnavailableError:
        pass
    geo = None
    hostname = hop.get("hostname")
    if hostname:
        inferred = city_from_hostname(hostname)
        if inferred:
            geo = {**inferred, "confidence": "inferred"}
    if geo is None:
        try:
            declared = geofeed_for_ip(ip)
        except GeoDataUnavailableError:
            declared = None
        if declared:
            geo = {**declared, "method": "rfc8805-geofeed", "confidence": "declared"}
    if geo is None:
        try:
            registry = country_for_ip(ip)
        except GeoDataUnavailableError:
            registry = None
        if registry:
            geo = {**registry, "method": "rir-delegated", "confidence": "country-only"}
    if geo and vp_loc:
        coords = None
        if geo.get("lat") is not None and geo.get("lng") is not None:
            coords = (geo["lat"], geo["lng"])
        elif geo.get("city"):
            coords = city_coordinates(geo["city"], geo.get("country"))
        if coords:
            check = rtt_consistency(
                vp_loc.get("lat"),
                vp_loc.get("lon"),
                coords[0],
                coords[1],
                hop.get("rtt_ms"),
            )
            if check:
                geo["rtt_check"] = check
                if not check["consistent"]:
                    geo["confidence"] = "contradicted-by-rtt"
    enriched["geo"] = geo
    return enriched


@envelope("matthewpp.ark.geo.enrich_result")
def enrich_result(result_id: str) -> Result:
    """Annotate a stored measurement with ASN and geolocation, and save it back.

    Enrichment is a separate step from measurement on purpose. The measurement
    cost packets and cannot be cheaply repeated; annotation is free and depends
    on datasets that are updated daily. Keeping them apart means a measurement
    is never lost to an enrichment failure, and can be re-enriched later against
    better data without re-probing.

    The enrichment is written into the stored record under ``enrichment``,
    alongside the snapshot versions of every dataset used, so an annotation can
    always be traced to the data that produced it.

    Args:
        result_id: As returned by `list_results`.

    Returns:
        ``data`` is ``{"result_id", "hops_annotated", "ips_annotated",
        "sources"}``.

    Raises:
        UnknownResultIdError: no such result.
    """
    record = get_result(result_id)["data"]
    data = record.get("data")
    hops = ips = 0

    if isinstance(data, dict) and isinstance(data.get("traces"), list):
        vp_locations = data.get("vp_locations") or {}
        for trace in data["traces"]:
            vp_loc = vp_locations.get(trace.get("vp_id"))
            trace["hops"] = [_annotate_hop(hop, vp_loc) for hop in trace.get("hops", [])]
            hops += len(trace["hops"])
    elif isinstance(data, list):
        for rec in data:
            if isinstance(rec, dict) and rec.get("target_ip"):
                rec["target_geo"] = _annotate_hop({"ip": rec["target_ip"]}).get("geo")
                ips += 1

    record["enrichment"] = {
        "enriched_at_utc": now_iso(),
        "hops_annotated": hops,
        "ips_annotated": ips,
        "sources": source_versions(),
    }
    path = results_dir() / next(r["path"] for r in _read_index() if r["result_id"] == result_id)
    path.write_text(json.dumps(record, indent=2, default=str))
    return Result(
        data={"result_id": result_id, "hops_annotated": hops, "ips_annotated": ips, "path": str(path)},
        provenance_extra={"sources": record["enrichment"]["sources"]},
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser with one subcommand per public
    function in this module."""
    parser = argparse.ArgumentParser(prog="ark_measurement", description=__doc__)
    parser.add_argument("--format", choices=["json", "csv", "md"], default="json")
    parser.add_argument("--output-file", default=None, help="write output here instead of stdout")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("vps", help="list Ark VPs matching filters")
    _add_vp_filter_args(p)
    p.add_argument("--limit", type=int)

    p = sub.add_parser("vp", help="fetch a single VP by ID")
    p.add_argument("--vp-id", required=True)

    p = sub.add_parser("count-vps", help="count VPs matching filters")
    _add_vp_filter_args(p)

    p = sub.add_parser("nearest-vps", help="find nearest VPs to a point")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("-n", type=int, default=5)

    p = sub.add_parser(
        "ping",
        help=(
            f"ping a target from matching VPs (max {MAX_PING_DURATION_S:.0f}s / "
            f"{MAX_PING_COUNT} probes / {MAX_VPS_PER_MEASUREMENT} VPs)"
        ),
    )
    p.add_argument("--target", required=True)
    _add_vp_filter_args(p)
    p.add_argument("--duration-s", type=float, default=10.0)
    p.add_argument("--count", type=int)
    p.add_argument("--interval-ms", type=int, default=1000)
    p.add_argument("--timeout-ms", type=int, default=2000)
    p.add_argument("--method", choices=sorted(_SUPPORTED_PING_METHODS), default="icmp-echo")
    p.add_argument("--sequential", dest="parallel", action="store_false", default=True)

    p = sub.add_parser("is-reachable", help="single-probe reachability check")
    p.add_argument("--target", required=True)
    _add_vp_filter_args(p)
    p.add_argument("--timeout-ms", type=int, default=2000)

    p = sub.add_parser(
        "ping-multi", help=f"ping several targets (max {MAX_MULTI_TARGETS}) from the same VP set"
    )
    p.add_argument("--targets", required=True, help="comma-separated list of targets")
    _add_vp_filter_args(p)
    p.add_argument("--duration-s", type=float, default=10.0)

    p = sub.add_parser("compare-ping-regions", help="compare avg latency between two regions")
    p.add_argument("--target", required=True)
    p.add_argument("--region-a", required=True)
    p.add_argument("--region-b", required=True)
    p.add_argument("--duration-s", type=float, default=10.0)

    p = sub.add_parser(
        "traceroute",
        help=(
            f"traceroute a target from matching VPs (max {MAX_TRACEROUTE_HOPS} hops / "
            f"{MAX_VPS_PER_MEASUREMENT} VPs)"
        ),
    )
    p.add_argument("--target", required=True)
    _add_vp_filter_args(p)
    p.add_argument("--method", choices=sorted(_SUPPORTED_TR_METHODS), default="icmp-paris")
    p.add_argument("--max-hops", type=int, default=32)
    p.add_argument("--attempts-per-hop", type=int, default=2)
    p.add_argument("--wait-ms", type=int, default=1000)

    p = sub.add_parser("compare-paths", help="diff a fresh traceroute vs. a baseline / flag suspect transit")
    p.add_argument("--target", required=True)
    _add_vp_filter_args(p)
    p.add_argument("--baseline-run-id")
    p.add_argument("--flag-transit-asn", type=int)
    p.add_argument("--flag-transit-prefix")

    p = sub.add_parser("check-transit", help="one-shot: does the current path transit a suspect AS/prefix?")
    p.add_argument("--target", required=True)
    p.add_argument("--transit-asn", type=int, help="AS number to check for unexpected transit")
    p.add_argument("--transit-prefix", help="CIDR prefix to check for unexpected transit")
    _add_vp_filter_args(p)

    p = sub.add_parser("list-hops-by-asn", help="list hops belonging to an AS within a stored run")
    p.add_argument("--run-id", required=True)
    p.add_argument("--asn", type=int, required=True)

    p = sub.add_parser("get-run", help="fetch a stored traceroute run")
    p.add_argument("--run-id", required=True)

    p = sub.add_parser("list-runs", help="list recent stored traceroute runs")
    p.add_argument("--target")
    p.add_argument("--vp-id")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("save-baseline", help="label a stored run as a named baseline")
    p.add_argument("--run-id", required=True)
    p.add_argument("--label", required=True)

    p = sub.add_parser("dns", help="query DNS from matching VPs")
    p.add_argument("--qname", required=True)
    p.add_argument("--qtype", choices=sorted(_SUPPORTED_QTYPES), default="A")
    _add_vp_filter_args(p)
    p.add_argument("--resolver", default="system")

    p = sub.add_parser("dns-divergence", help="group DNS answers by distinct answer set")
    p.add_argument("--qname", required=True)
    p.add_argument("--qtype", choices=sorted(_SUPPORTED_QTYPES), default="A")
    _add_vp_filter_args(p)

    p = sub.add_parser("dnssec-check", help="simplified per-VP DNSSEC presence check")
    p.add_argument("--qname", required=True)
    _add_vp_filter_args(p)

    p = sub.add_parser(
        "diagnose", help=f"multi-VP root-cause diagnostic (max {MAX_DIAGNOSE_SAMPLE_SIZE} VP sample)"
    )
    p.add_argument("--target", required=True)
    p.add_argument("--suspect-asn", type=int)
    p.add_argument("--suspect-prefix")
    _add_vp_filter_args(p)
    p.add_argument("--sample-size", type=int, default=15)
    p.add_argument("--no-bgp-context", dest="include_bgp_context", action="store_false", default=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "vps":
        resp = list_vps(**_vp_filter_from_args(args), limit=args.limit)
    elif args.command == "vp":
        resp = get_vp_by_id(args.vp_id)
    elif args.command == "count-vps":
        f = _vp_filter_from_args(args)
        resp = count_vps(
            region=f["region"], country=f["country"], asn=f["asn"], tag=f["tag"], active_only=f["active_only"]
        )
    elif args.command == "nearest-vps":
        resp = nearest_vps(args.lat, args.lon, n=args.n)
    elif args.command == "ping":
        resp = ping(
            args.target,
            vp_filter=_vp_filter_from_args(args),
            duration_s=args.duration_s,
            count=args.count,
            interval_ms=args.interval_ms,
            timeout_ms=args.timeout_ms,
            method=args.method,
            parallel=args.parallel,
        )
    elif args.command == "is-reachable":
        resp = is_reachable(args.target, vp_filter=_vp_filter_from_args(args), timeout_ms=args.timeout_ms)
    elif args.command == "ping-multi":
        resp = ping_multi_targets(
            [t.strip() for t in args.targets.split(",") if t.strip()],
            vp_filter=_vp_filter_from_args(args),
            duration_s=args.duration_s,
        )
    elif args.command == "compare-ping-regions":
        resp = compare_ping_regions(args.target, args.region_a, args.region_b, duration_s=args.duration_s)
    elif args.command == "traceroute":
        resp = traceroute(
            args.target,
            vp_filter=_vp_filter_from_args(args),
            method=args.method,
            max_hops=args.max_hops,
            attempts_per_hop=args.attempts_per_hop,
            wait_ms=args.wait_ms,
        )
    elif args.command == "compare-paths":
        resp = compare_paths(
            args.target,
            vp_filter=_vp_filter_from_args(args),
            baseline_run_id=args.baseline_run_id,
            flag_transit_asn=args.flag_transit_asn,
            flag_transit_ip_prefix=args.flag_transit_prefix,
        )
    elif args.command == "check-transit":
        resp = check_transit(
            args.target,
            asn=args.transit_asn,
            ip_prefix=args.transit_prefix,
            vp_filter=_vp_filter_from_args(args),
        )
    elif args.command == "list-hops-by-asn":
        resp = list_hops_by_asn(args.run_id, args.asn)
    elif args.command == "get-run":
        resp = get_run(args.run_id)
    elif args.command == "list-runs":
        resp = list_runs(target=args.target, vp_id=args.vp_id, limit=args.limit)
    elif args.command == "save-baseline":
        resp = save_run_as_baseline(args.run_id, args.label)
    elif args.command == "dns":
        resp = dns_query(
            args.qname, qtype=args.qtype, vp_filter=_vp_filter_from_args(args), resolver=args.resolver
        )
    elif args.command == "dns-divergence":
        resp = dns_divergence_report(args.qname, qtype=args.qtype, vp_filter=_vp_filter_from_args(args))
    elif args.command == "dnssec-check":
        resp = check_dnssec_valid(args.qname, vp_filter=_vp_filter_from_args(args))
    elif args.command == "diagnose":
        resp = diagnose_path_anomaly(
            args.target,
            suspect_asn=args.suspect_asn,
            suspect_ip_prefix=args.suspect_prefix,
            vp_filter=_vp_filter_from_args(args),
            sample_size=args.sample_size,
            include_bgp_context=args.include_bgp_context,
        )
    else:  # pragma: no cover - argparse enforces `choices` above
        parser.error(f"unknown command: {args.command}")
        return 2

    rendered = write_output(resp, path=args.output_file, output_format=args.format)
    if args.output_file is None:
        print(rendered)
    return 0 if resp["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
