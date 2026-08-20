"""geodata — IP metadata sources for enriching Ark measurements.

Answers four questions about an IP address or a router hostname, each from a
different public dataset:

===================  ==================================  =====================
question             dataset                             granularity
===================  ==================================  =====================
which AS?            RouteViews prefix2as (CAIDA)        prefix -> ASN
which city?          RFC 8805 geofeeds (CAIDA harvest)   prefix -> city
which city? (router) hoiho geo-re (CAIDA ITDK)           hostname -> city
which country?       RIR delegated-extended files        prefix -> country
===================  ==================================  =====================

None of these require credentials. Each resolves through the same three-step
search so the toolkit works both on an Ark host and on a laptop that only has
mux access:

    1. a local CAIDA mount (/data/...) if present -- fastest and freshest
    2. a previously downloaded copy under $MATTHEWPP_GEODATA_DIR
    3. download it now, then cache

A dataset that cannot be resolved raises `GeoDataUnavailableError` rather than
returning empty results, because "no ASN for this hop" and "the ASN dataset
isn't installed" must never look the same to a caller.
"""

from __future__ import annotations

import bz2
import csv
import gzip
import ipaddress
import json
import os
import re
import time
import urllib.request
from pathlib import Path

from .core import MatthewPPError

# --- where things live -----------------------------------------------------

LOCAL_PREFIX2AS = Path("/data/routing/routeviews-prefix2as/routeviews-rv2-latest.pfx2as.gz")
LOCAL_GEOFEED_ROOT = Path("/data/external/geofeed-whois")
LOCAL_ITDK_ROOT = Path("/data/topology/ITDK-public")

PUBLIC_BASE = "https://publicdata.caida.org/datasets"
RIR_DELEGATED_URLS = {
    "arin": "https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest",
    "ripencc": "https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-extended-latest",
    "apnic": "https://ftp.apnic.net/apnic/stats/apnic/delegated-apnic-extended-latest",
    "lacnic": "https://ftp.lacnic.net/pub/stats/lacnic/delegated-lacnic-extended-latest",
    "afrinic": "https://ftp.afrinic.net/pub/stats/afrinic/delegated-afrinic-extended-latest",
}

# Geofeeds are operator-published and occasionally contain typos. A handful of
# real entries declare a single city for an implausibly large block -- one
# 2026-08 record claims 206.163.25.0/2, a quarter of the IPv4 internet, is
# Minneapolis. Left in, a single typo silently mislocates a billion addresses,
# so anything broader than these is dropped. Legitimate geofeeds are far more
# specific (the bulk are /24 or longer).
MIN_GEOFEED_PREFIXLEN_V4 = 12
MIN_GEOFEED_PREFIXLEN_V6 = 24

# Datasets are refreshed daily upstream; a week-old cache is still useful and
# re-downloading on every call would be hostile to the mirrors.
CACHE_MAX_AGE_S = 7 * 24 * 3600


class GeoDataUnavailableError(MatthewPPError):
    """A required dataset could not be found locally or downloaded."""


def geodata_dir() -> Path:
    default = (
        Path(os.environ["OUTPUT_DIR"]) / "geodata"
        if os.environ.get("OUTPUT_DIR")
        else Path.home() / ".caida-ai-ops" / "geodata"
    )
    return Path(os.environ.get("MATTHEWPP_GEODATA_DIR", str(default))).expanduser()


def _cached(name: str, url: str, max_age_s: int = CACHE_MAX_AGE_S) -> Path:
    """Return a cached copy of ``url``, downloading it if absent or stale."""
    path = geodata_dir() / name
    if path.exists() and (time.time() - path.stat().st_mtime) < max_age_s:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
        tmp.replace(path)  # atomic: a killed download never leaves a torn cache
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        if path.exists():
            return path  # stale beats nothing
        raise GeoDataUnavailableError(f"could not download {url}: {exc}") from exc
    return path


# --- longest-prefix-match index -------------------------------------------


class PrefixIndex:
    """Longest-prefix-match lookup over IPv4/IPv6 prefixes.

    One dict per prefix length, probed longest-first. Simpler than a radix
    tree and fast enough: at most 33 (or 129) dict lookups per query, and the
    build cost is linear in the number of prefixes.
    """

    def __init__(self) -> None:
        self._v4: dict[int, dict[int, object]] = {}
        self._v6: dict[int, dict[int, object]] = {}

    def add(self, network: str, value: object) -> None:
        try:
            net = ipaddress.ip_network(network, strict=False)
        except ValueError:
            return
        table = self._v4 if net.version == 4 else self._v6
        table.setdefault(net.prefixlen, {})[int(net.network_address)] = value

    def lookup(self, ip: str) -> object | None:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        table = self._v4 if addr.version == 4 else self._v6
        bits = 32 if addr.version == 4 else 128
        val = int(addr)
        for length in sorted(table, reverse=True):
            masked = val & (((1 << length) - 1) << (bits - length))
            hit = table[length].get(masked)
            if hit is not None:
                return hit
        return None

    def __len__(self) -> int:
        return sum(len(t) for t in list(self._v4.values()) + list(self._v6.values()))


# --- dataset loaders (each cached in-process after first use) --------------

_cache: dict[str, object] = {}


def _source_note(path: Path, origin: str) -> dict:
    return {
        "path": str(path),
        "origin": origin,
        "mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime)),
    }


def load_prefix2as() -> tuple[PrefixIndex, dict]:
    """RouteViews prefix2as: IP -> origin ASN. Refreshed daily upstream."""
    if "prefix2as" in _cache:
        return _cache["prefix2as"]  # type: ignore[return-value]
    if LOCAL_PREFIX2AS.exists():
        path, origin = LOCAL_PREFIX2AS, "local:/data"
    else:
        path = _cached(
            "routeviews-latest.pfx2as.gz",
            f"{PUBLIC_BASE}/routing/routeviews-prefix2as/routeviews-rv2-latest.pfx2as.gz",
            max_age_s=24 * 3600,
        )
        origin = "download:publicdata.caida.org"
    idx = PrefixIndex()
    with gzip.open(path, "rt") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 3:
                continue
            # The ASN field can be an AS-set ("1234_5678") or multi-origin
            # ("1234,5678"); take the first, and record it as reported.
            asn = re.split(r"[,_]", parts[2])[0]
            if asn.isdigit():
                idx.add(f"{parts[0]}/{parts[1]}", int(asn))
    out = (idx, _source_note(path, origin))
    _cache["prefix2as"] = out
    return out


def _plausible_geofeed_prefix(prefix: str) -> bool:
    """Reject geofeed entries too broad to describe a single city."""
    try:
        net = ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return False
    floor = MIN_GEOFEED_PREFIXLEN_V4 if net.version == 4 else MIN_GEOFEED_PREFIXLEN_V6
    return net.prefixlen >= floor


def _latest_local_geofeed_day() -> Path | None:
    if not LOCAL_GEOFEED_ROOT.exists():
        return None
    days = sorted(LOCAL_GEOFEED_ROOT.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]"))
    return days[-1] if days else None


def load_geofeeds() -> tuple[PrefixIndex, dict[str, list[str]], dict]:
    """RFC 8805 geofeeds: prefixes with the operator's own city declaration.

    Returns a prefix index (ip -> location) and a reverse index
    (``"city, CC"`` -> prefixes) so a city can be turned into probe targets.
    """
    if "geofeeds" in _cache:
        return _cache["geofeeds"]  # type: ignore[return-value]
    day = _latest_local_geofeed_day()
    if day is None:
        # No CAIDA mount: fall back to the compact index built by
        # `build_geofeed_cache()` from the public mirror.
        rows = _load_geofeed_cache()
        if rows is None:
            raise GeoDataUnavailableError(
                "geofeed data is not available. There is no CAIDA mount at "
                "/data/external/geofeed-whois, and no cached index at "
                f"{_geofeed_cache_path()}. Build one once with "
                "`python -m caida_ai_ops.geodata build-geofeed-cache` (downloads ~5700 small "
                "files from publicdata.caida.org; a few minutes, then cached)."
            )
        idx = PrefixIndex()
        reverse = {}
        for prefix, cc, region, city in rows:
            loc = {"city": city, "region": region, "country": cc, "prefix": prefix}
            idx.add(prefix, loc)
            reverse.setdefault(f"{city.lower()}, {cc.lower()}", []).append(prefix)
        out = (idx, reverse, {"origin": "cache:public-mirror", "path": str(_geofeed_cache_path())})
        _cache["geofeeds"] = out
        return out
    idx = PrefixIndex()
    reverse: dict[str, list[str]] = {}
    for csv_path in day.glob("registries/*/*/*.csv"):
        try:
            with csv_path.open(newline="", errors="replace") as fh:
                # Operator-published files are not always clean: some carry NUL
                # bytes, which csv refuses outright. Strip them rather than lose
                # the whole registry's worth of data to one bad file.
                for row in csv.reader(line.replace("\0", "") for line in fh):
                    if len(row) < 4 or not row[0] or row[0].lstrip().startswith("#"):
                        continue
                    prefix, cc, region, city = row[0].strip(), row[1].strip(), row[2].strip(), row[3].strip()
                    if not city or not _plausible_geofeed_prefix(prefix):
                        continue
                    loc = {"city": city, "region": region, "country": cc, "prefix": prefix}
                    idx.add(prefix, loc)
                    reverse.setdefault(f"{city.lower()}, {cc.lower()}", []).append(prefix)
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
    out = (idx, reverse, _source_note(day, "local:/data"))
    _cache["geofeeds"] = out
    return out


def load_hoiho() -> tuple[list[dict], dict]:
    """CAIDA hoiho: learned regexes mapping router hostnames to places.

    Each entry is one operator domain with regexes that capture a location
    code, plus the geohints translating those codes to real places.
    """
    if "hoiho" in _cache:
        return _cache["hoiho"]  # type: ignore[return-value]
    local = sorted(LOCAL_ITDK_ROOT.glob("*/midar-iff-snmp.geo-re.jsonl*")) if LOCAL_ITDK_ROOT.exists() else []
    if local:
        path, origin = local[-1], "local:/data"
        opener = bz2.open if path.suffix == ".bz2" else open
    else:
        release = os.environ.get("MATTHEWPP_ITDK_RELEASE", "2025-03")
        path = _cached(
            "geo-re.jsonl",
            f"{PUBLIC_BASE}/topology/ark/ipv4/itdk/{release}/midar-iff-snmp.geo-re.jsonl",
            max_age_s=90 * 24 * 3600,
        )  # ITDK ships ~2x/year
        origin, opener = "download:publicdata.caida.org", open
    rules = []
    with opener(path, "rt", errors="replace") as fh:  # type: ignore[operator]
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            hints = {}
            for hint in entry.get("geohints", []):
                loc = hint.get("location") or {}
                if hint.get("code") and loc.get("place"):
                    try:
                        lat, lng = float(hint["lat"]), float(hint["lng"])
                    except (KeyError, TypeError, ValueError):
                        lat = lng = None
                    hints[hint["code"].lower()] = {
                        "city": loc.get("place"),
                        "region": loc.get("st"),
                        "country": loc.get("cc"),
                        "code_type": hint.get("type"),
                        "lat": lat,
                        "lng": lng,
                    }
            if not hints:
                continue
            rules.append(
                {
                    "domain": entry["domain"],
                    "regexes": [re.compile(r) for r in entry.get("re", [])],
                    "hints": hints,
                    "quality": entry.get("score", {}).get("class"),
                    "ppv": entry.get("score", {}).get("ppv"),
                }
            )
    out = (rules, _source_note(path, origin))
    _cache["hoiho"] = out
    return out


def load_rir_delegated() -> tuple[PrefixIndex, dict]:
    """RIR delegated-extended files: registry truth for IP -> country.

    Country granularity only, and it records where a block was *registered*,
    not where it is routed — treat it as a floor, not a location.
    """
    if "rir" in _cache:
        return _cache["rir"]  # type: ignore[return-value]
    idx = PrefixIndex()
    fetched = []
    for rir, url in RIR_DELEGATED_URLS.items():
        try:
            path = _cached(f"delegated-{rir}-extended-latest", url, max_age_s=24 * 3600)
        except GeoDataUnavailableError:
            continue  # one unreachable RIR must not sink the other four
        fetched.append(rir)
        with path.open(errors="replace") as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                f = line.strip().split("|")
                if len(f) < 7 or f[2] not in ("ipv4", "ipv6") or f[6] not in ("assigned", "allocated"):
                    continue
                cc, start, count = f[1], f[3], f[4]
                try:
                    if f[2] == "ipv4":
                        # IPv4 records give a host count, not a prefix length.
                        length = 32 - int(count).bit_length() + 1
                        idx.add(f"{start}/{length}", {"country": cc, "rir": rir})
                    else:
                        idx.add(f"{start}/{count}", {"country": cc, "rir": rir})
                except (ValueError, TypeError):
                    continue
    if not fetched:
        raise GeoDataUnavailableError("no RIR delegated files could be fetched")
    out = (idx, {"origin": "download:rir-ftp", "rirs": fetched})
    _cache["rir"] = out
    return out


# --- lookups ---------------------------------------------------------------


def address_scope(ip: str) -> str | None:
    """Classify an address that should not be geolocated at all.

    Private, loopback, link-local, multicast and reserved addresses have no
    meaningful location -- a hop at 10.1.62.49 is inside somebody's network,
    not in a city. Returning a scope here lets callers say so explicitly
    instead of reporting whatever a stray prefix match produced.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "invalid"
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link-local"
    if addr.is_multicast:
        return "multicast"
    if addr.is_private:
        return "private"
    if addr.is_reserved or addr.is_unspecified:
        return "reserved"
    return None


def asn_for_ip(ip: str) -> int | None:
    idx, _ = load_prefix2as()
    return idx.lookup(ip)  # type: ignore[return-value]


def geofeed_for_ip(ip: str) -> dict | None:
    if address_scope(ip):
        return None
    idx, _, _ = load_geofeeds()
    return idx.lookup(ip)  # type: ignore[return-value]


def country_for_ip(ip: str) -> dict | None:
    if address_scope(ip):
        return None
    idx, _ = load_rir_delegated()
    return idx.lookup(ip)  # type: ignore[return-value]


def city_from_hostname(hostname: str) -> dict | None:
    """Infer a city from a router hostname using CAIDA's learned hoiho rules.

    Returns None when no rule covers the hostname's domain, or the rule
    matches but the captured code has no geohint — both are common, and
    guessing past them is how bad geolocation happens.
    """
    if not hostname:
        return None
    rules, _ = load_hoiho()
    host = hostname.lower().rstrip(".")
    for rule in rules:
        if not (host == rule["domain"] or host.endswith("." + rule["domain"])):
            continue
        for rx in rule["regexes"]:
            match = rx.match(host)
            if not match or not match.groups():
                continue
            hint = rule["hints"].get(match.group(1).lower())
            if hint:
                return {
                    **hint,
                    "method": f"hoiho:{rule['domain']}",
                    "code": match.group(1),
                    "rule_quality": rule["quality"],
                    "rule_ppv": rule["ppv"],
                }
    return None


def prefixes_in_city(city: str, country: str | None = None) -> list[str]:
    """Prefixes whose operator declares them to be in ``city``."""
    _, reverse, _ = load_geofeeds()
    key_city = city.strip().lower()
    if country:
        return list(reverse.get(f"{key_city}, {country.strip().lower()}", []))
    out: list[str] = []
    for key, prefixes in reverse.items():
        if key.split(",")[0].strip() == key_city:
            out.extend(prefixes)
    return out


def source_versions() -> dict:
    """Which snapshot of each dataset is currently loaded, for provenance."""
    versions = {}
    for key, loader in (
        ("prefix2as", load_prefix2as),
        ("geofeeds", load_geofeeds),
        ("hoiho", load_hoiho),
        ("rir_delegated", load_rir_delegated),
    ):
        try:
            versions[key] = loader()[-1]
        except (GeoDataUnavailableError, OSError) as exc:
            versions[key] = {"unavailable": str(exc)[:160]}
    return versions


# --- distance / physics ----------------------------------------------------

# Light in fibre travels at roughly two-thirds of c. A round trip over a given
# distance therefore has a hard minimum RTT that nothing can beat -- no routing,
# no hardware. That makes RTT the only source here capable of *refuting* a
# location claim: every other dataset can only assert one.
C_FIBRE_KM_PER_S = 3e5 * 0.66

# Real paths are not great circles and queueing inflates RTT, so the bound is
# only ever used to reject claims that are physically impossible, never to
# confirm one. A small tolerance absorbs clock and measurement noise.
RTT_TOLERANCE = 0.90


def min_rtt_ms_for_km(km: float) -> float:
    """Fastest possible round trip over a great-circle distance, in ms."""
    return (2.0 * km) / C_FIBRE_KM_PER_S * 1000.0


def city_coordinates(city: str, country: str | None = None) -> tuple[float, float] | None:
    """Coordinates for a city, from the geohints inside CAIDA's hoiho rules.

    Not a general gazetteer -- it covers the cities that appear in operator
    router names, which is exactly the population that shows up in traceroute
    hops, and it costs nothing extra since the file is already loaded.
    """
    if "hoiho_gazetteer" not in _cache:
        rules, _ = load_hoiho()
        gaz: dict[tuple[str, str], tuple[float, float]] = {}
        for rule in rules:
            for hint in rule["hints"].values():
                if hint.get("lat") is None or not hint.get("city"):
                    continue
                key = (hint["city"].lower(), (hint.get("country") or "").lower())
                gaz.setdefault(key, (hint["lat"], hint["lng"]))
        _cache["hoiho_gazetteer"] = gaz
    gaz = _cache["hoiho_gazetteer"]  # type: ignore[assignment]
    city_l = city.strip().lower()
    if country:
        hit = gaz.get((city_l, country.strip().lower()))
        if hit:
            return hit
    for (name, _cc), coords in gaz.items():
        if name == city_l:
            return coords
    return None


def rtt_consistency(
    vp_lat: float | None,
    vp_lon: float | None,
    claim_lat: float | None,
    claim_lon: float | None,
    rtt_ms: float | None,
) -> dict | None:
    """Is a location claim physically reachable in the RTT observed?

    Returns ``{"consistent": bool, "min_possible_ms", "observed_ms",
    "distance_km"}``, or None when the check cannot be made (unknown
    coordinates, or no reply to time).

    ``consistent: False`` means the claim is *impossible* -- the packet would
    have had to outrun light. ``consistent: True`` means only that the claim is
    not excluded: an RTT of 3 ms from San Diego is equally consistent with Los
    Angeles, Orange County, and San Diego itself.
    """
    if None in (vp_lat, vp_lon, claim_lat, claim_lon, rtt_ms) or rtt_ms <= 0:
        return None
    from .core import haversine_km

    km = haversine_km(vp_lat, vp_lon, claim_lat, claim_lon)
    floor = min_rtt_ms_for_km(km)
    return {
        "consistent": rtt_ms >= floor * RTT_TOLERANCE,
        "min_possible_ms": round(floor, 2),
        "observed_ms": round(rtt_ms, 2),
        "distance_km": round(km, 1),
    }


# --- geofeed cache for hosts without the CAIDA mount -----------------------
#
# The public mirror publishes each operator's geofeed as its own small CSV --
# roughly 5700 of them. Fetching those per query would be absurd, so they are
# downloaded once, parsed, and written out as a single compact index. After
# that a machine with only mux access behaves exactly like an Ark host.


def _geofeed_cache_path() -> Path:
    return geodata_dir() / "geofeeds-index.jsonl.gz"


def _load_geofeed_cache() -> list[tuple[str, str, str, str]] | None:
    path = _geofeed_cache_path()
    if not path.exists():
        return None
    rows = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            try:
                rows.append(tuple(json.loads(line)))  # type: ignore[arg-type]
            except json.JSONDecodeError:
                continue
    return rows or None


def _public_geofeed_day() -> str:
    """Most recent day directory on the public mirror, as YYYY/MM/DD."""

    def _links(url: str, pattern: str) -> list[str]:
        with urllib.request.urlopen(url, timeout=60) as resp:
            body = resp.read().decode("utf-8", "replace")
        return sorted(set(re.findall(pattern, body)))

    base = f"{PUBLIC_BASE}/geofeed-whois/"
    year = _links(base, r'href="(\d{4})/"')[-1]
    month = _links(f"{base}{year}/", r'href="(\d{2})/"')[-1]
    day = _links(f"{base}{year}/{month}/", r'href="(\d{2})/"')[-1]
    return f"{year}/{month}/{day}"


def build_geofeed_cache(max_workers: int = 16, progress: bool = True) -> dict:
    """Download the public geofeed corpus once and write a compact local index.

    Only needed on hosts without /data. Returns a summary dict.
    """
    from concurrent.futures import ThreadPoolExecutor

    day = _public_geofeed_day()
    root = f"{PUBLIC_BASE}/geofeed-whois/{day}/registries"
    urls: list[str] = []
    with urllib.request.urlopen(root + "/", timeout=60) as resp:
        registries = sorted(set(re.findall(r'href="([a-z]+)/"', resp.read().decode("utf-8", "replace"))))
    for registry in registries:
        for kind in ("standard", "non_standard"):
            listing = f"{root}/{registry}/{kind}/"
            try:
                with urllib.request.urlopen(listing, timeout=60) as resp:
                    body = resp.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - a registry may publish neither kind
                continue
            urls += [listing + name for name in sorted(set(re.findall(r'href="([^"/]+\.csv)"', body)))]

    def fetch(url: str) -> list[tuple[str, str, str, str]]:
        try:
            with urllib.request.urlopen(url, timeout=45) as resp:
                text = resp.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - one operator's server being down is normal
            return []
        found = []
        for row in csv.reader(line.replace("\0", "") for line in text.splitlines()):
            if len(row) < 4 or not row[0] or row[0].lstrip().startswith("#"):
                continue
            prefix, cc, region, city = (row[0].strip(), row[1].strip(), row[2].strip(), row[3].strip())
            if city and _plausible_geofeed_prefix(prefix):
                found.append((prefix, cc, region, city))
        return found

    rows: list[tuple[str, str, str, str]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for i, chunk in enumerate(pool.map(fetch, urls)):
            rows.extend(chunk)
            if progress and i % 500 == 0:
                print(f"  {i}/{len(urls)} files, {len(rows)} prefixes", flush=True)

    path = _geofeed_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    with gzip.open(tmp, "wt") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    tmp.replace(path)
    _cache.pop("geofeeds", None)
    return {"day": day, "files": len(urls), "prefixes": len(rows), "path": str(path)}


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "build-geofeed-cache":
        print(json.dumps(build_geofeed_cache(), indent=2))
    else:
        print(json.dumps(source_versions(), indent=2, default=str))
