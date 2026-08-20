"""itdk_db — read-only CAIDA ITDK topology queries for Matthew++.

Deliberately scoped to exactly the three queries used by the
``nids-itdk-jaber-the-great`` assignment notebook (Level3/Netflix
geo-adjacent links, China Unicom vs. Level3 country footprint + West Coast
peering, and the 18-AS pairwise link-count matrix) — see that repo's
``Task-1.md``/``Task-2.md``/``Task-3.md`` for the exact SQL each function
below mirrors. This is not a general-purpose ITDK query API; it does not
attempt to support arbitrary ASes/questions beyond what those three tasks
actually ask.

Two backends, selected automatically:

- **Live backend** (default): connects to CAIDA's shared ``caida_itdk``
  Postgres database via SQLAlchemy, using the exact credential convention
  from ``nids-itdk-jaber-the-great/Datasets.md``: a ``db_credentials.env``
  file (never read/printed by this module — only handed to
  ``python-dotenv`` to load) providing ``ITDK_READ_DSN``, with
  ``ITDK_READ_DSN``/``ITDK_CREDS_FILE`` environment variables able to
  override the DSN/file location. The schema (``itdk_node_as``,
  ``itdk_node_geolocation``, ``itdk_link_endpoints``,
  ``itdk_router_hostnames``) already exists and is pre-populated by CAIDA —
  nothing here creates tables.
- **Demo backend** (``MATTHEWPP_DEMO=1``): swaps in a small, fixed set of
  synthetic nodes/links (including a Level3/Netflix example) so every
  function here can be exercised — and the CLI demoed — with no database
  access at all. Every demo-mode response carries an explicit warning.

Output contract: every public function returns
``{"status", "function", "parameters", "data", "warnings", "provenance"}``
(see ``matthewpp_core.envelope``/``Result``).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .core import (
    MatthewPPError,
    Result,
    demo_warning,
    envelope,
    haversine_km,
    is_demo_mode,
    write_output,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DbConnectionError(MatthewPPError):
    """The ITDK Postgres database was unreachable or misconfigured."""


# ---------------------------------------------------------------------------
# The 18 ASes from Task 3 (nids-itdk-jaber-the-great/Task-3.md) — kept here
# as the default AS list / name+category labels for `task3_pairwise_link_counts`.
# ---------------------------------------------------------------------------

ASES: dict[int, str] = {
    174: "Cogent",
    701: "Verizon",
    1299: "Arelion",
    3257: "GTT",
    3491: "PCCW",
    5511: "Orange",
    6453: "TATA",
    3320: "Deutsche Telekom",
    6461: "Zayo",
    6762: "Telecom Italia",
    6830: "Liberty Global",
    12956: "Telefonica",
    15133: "Edgecast",
    20940: "Akamai",
    714: "Apple",
    2906: "Netflix",
    13335: "Cloudflare",
    15169: "Google",
}

AS_CATEGORY: dict[int, str] = {
    174: "I",
    701: "I",
    1299: "I",
    3257: "I",
    3491: "I",
    5511: "I",
    6453: "I",
    3320: "I",
    6461: "I",
    6762: "I",
    6830: "I",
    12956: "I",
    15133: "D",
    20940: "D",
    714: "C",
    2906: "C",
    13335: "C",
    15169: "C",
}

DEFAULT_18_ASNS: list[int] = list(ASES.keys())


def _demo_warning() -> list[str]:
    return demo_warning("ITDK")


# ---------------------------------------------------------------------------
# Demo fixtures (used only when MATTHEWPP_DEMO is truthy)
# ---------------------------------------------------------------------------

# node_id -> asn (mirrors itdk_node_as)
_DEMO_NODE_AS: dict[str, int] = {
    "N1001": 3356,
    "N1002": 3356,
    "N1003": 3356,
    "N1004": 3356,  # Level3 (N1004 has no geo row)
    "N2001": 2906,
    "N2002": 2906,
    "N2003": 2906,
    "N2004": 2906,  # Netflix
    "N3001": 4837,
    "N3002": 4837,
    "N3003": 4837,  # China Unicom (N3001/N3002 = US West Coast)
    "N4001": 174,
    "N4002": 701,
    "N4003": 15169,  # Cogent, Verizon, Google (Task 3 sample)
}

# node_id -> geolocation (mirrors itdk_node_geolocation; a missing key = no geolocation row)
_DEMO_NODE_GEO: dict[str, dict] = {
    "N1001": {
        "continent": "North America",
        "country": "US",
        "region": "DC",
        "city": "Washington",
        "latitude": 38.9072,
        "longitude": -77.0369,
    },
    "N1002": {
        "continent": "North America",
        "country": "US",
        "region": "DC",
        "city": "Washington",
        "latitude": 38.9500,
        "longitude": -77.0000,
    },
    "N1003": {
        "continent": "North America",
        "country": "US",
        "region": "CA",
        "city": "San Jose",
        "latitude": 37.3382,
        "longitude": -121.8863,
    },
    "N2001": {
        "continent": "North America",
        "country": "US",
        "region": "DC",
        "city": "Washington",
        "latitude": 38.9100,
        "longitude": -77.0400,
    },
    "N2002": {
        "continent": "North America",
        "country": "US",
        "region": "CA",
        "city": "San Jose",
        "latitude": 37.3400,
        "longitude": -121.8900,
    },
    "N2003": {
        "continent": "North America",
        "country": "US",
        "region": "NY",
        "city": "New York",
        "latitude": 40.7128,
        "longitude": -74.0060,
    },
    "N2004": {
        "continent": "Europe",
        "country": "GB",
        "region": None,
        "city": "London",
        "latitude": 51.5072,
        "longitude": -0.1276,
    },
    "N3001": {
        "continent": "North America",
        "country": "US",
        "region": "CA",
        "city": "San Francisco",
        "latitude": 37.7749,
        "longitude": -122.4194,
    },
    "N3002": {
        "continent": "North America",
        "country": "US",
        "region": "CA",
        "city": "Los Angeles",
        "latitude": 34.0522,
        "longitude": -118.2437,
    },
    "N3003": {
        "continent": "Asia",
        "country": "CN",
        "region": None,
        "city": "Beijing",
        "latitude": 39.9042,
        "longitude": 116.4074,
    },
    # N1004, N4001-N4003 intentionally have no geolocation row in this fixture.
}

# link_id -> member node_ids (>2 nodes would model a hyperlink; none in this small fixture)
_DEMO_LINK_ENDPOINTS: dict[str, list[str]] = {
    "L0001": ["N1001", "N2001"],  # Level3-Netflix, Washington DC (adjacent)
    "L0002": ["N1003", "N2002"],  # Level3-Netflix, San Jose (adjacent)
    "L0003": ["N1002", "N2003"],  # Level3-Netflix, DC <-> NYC (non-adjacent)
    "L0004": ["N1004", "N2003"],  # Level3 (ungeolocated) - Netflix; excluded from geo results
    "L0005": ["N1001", "N2004"],  # Level3-Netflix, DC <-> London (non-adjacent)
    "L0006": ["N3001", "N4001"],  # China Unicom (SF) - Cogent
    "L0007": ["N3001", "N4002"],  # China Unicom (SF) - Verizon
    "L0008": ["N3002", "N4001"],  # China Unicom (LA) - Cogent
    "L0009": ["N2003", "N4003"],  # Netflix (2906) - Google (15169): both in the Task 3 18-AS list
}

# node_id -> PTR hostname, simplified for the demo (real schema keys hostnames by
# interface IP via itdk_router_hostnames; None = no PTR record, as is common).
_DEMO_HOSTNAMES: dict[str, str | None] = {
    "N4001": "if-0.core1.sjc.cogentco.com",
    "N4002": None,
}


# ---------------------------------------------------------------------------
# Backend accessor — lazy import, fail loudly, demo-mode short-circuit.
# ---------------------------------------------------------------------------


def _get_engine():
    """Return a live SQLAlchemy engine connected to ``caida_itdk``, or raise.

    Mirrors ``nids-itdk-jaber-the-great/Datasets.md``'s exact connection
    recipe: read ``ITDK_READ_DSN`` from the environment, or from a
    ``db_credentials.env`` file (path from ``ITDK_CREDS_FILE``, default
    ``./db_credentials.env``) via ``python-dotenv`` — the file itself is
    never read into this function's return value or logged, only handed to
    ``dotenv_values`` to extract the one key needed. Not called in demo
    mode.
    """
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise DbConnectionError(
            "sqlalchemy is not installed. Set MATTHEWPP_DEMO=1 to exercise this "
            "function against synthetic fixtures, or `pip install sqlalchemy psycopg2-binary`."
        ) from exc

    dsn = os.environ.get("ITDK_READ_DSN")
    if not dsn:
        try:
            from dotenv import dotenv_values
        except ImportError as exc:
            raise DbConnectionError(
                "python-dotenv is not installed (needed to read db_credentials.env)"
            ) from exc
        creds_file = Path(os.environ.get("ITDK_CREDS_FILE", "./db_credentials.env"))
        creds = dotenv_values(creds_file) if creds_file.exists() else {}
        dsn = creds.get("ITDK_READ_DSN")
    if not dsn:
        raise DbConnectionError(
            "no ITDK_READ_DSN found. Set MATTHEWPP_DEMO=1 to exercise this function against "
            "synthetic fixtures, or provide a db_credentials.env (see "
            "nids-itdk-jaber-the-great/db_credentials.env.example) with ITDK_READ_DSN set, "
            "or export ITDK_READ_DSN directly."
        )
    try:
        return create_engine(dsn)
    except Exception as exc:  # noqa: BLE001
        raise DbConnectionError(f"could not create an engine for caida_itdk: {exc}") from exc


def _run_query(sql: str, params: dict | None = None) -> list[dict]:
    """Run a parameterized SQL query against ``caida_itdk`` and return rows
    as plain dicts (never a DataFrame — this toolkit's output contract is
    JSON-serializable dicts throughout). Not called in demo mode."""
    from sqlalchemy import text

    engine = _get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]


# ---------------------------------------------------------------------------
# Shared classification/ranking helpers (pure logic; identical for both
# the demo and live code paths, so behavior never drifts between them)
# ---------------------------------------------------------------------------


def _classify_geo_rows(rows: list[dict], threshold_km: float) -> dict:
    for row in rows:
        row["distance_km"] = haversine_km(row["a_lat"], row["a_lon"], row["b_lat"], row["b_lon"])
    adjacent = [r for r in rows if r["distance_km"] <= threshold_km]
    non_adjacent = [r for r in rows if r["distance_km"] > threshold_km]

    locations: dict[tuple, set] = {}
    for r in adjacent:
        key = (r["a_city"], r["a_country"])
        locations.setdefault(key, set()).add(r["link_id"])
    peering_locations = sorted(
        (
            {"city": city, "country": country, "link_count": len(link_ids)}
            for (city, country), link_ids in locations.items()
        ),
        key=lambda x: -x["link_count"],
    )

    return {
        "adjacent_count": len(adjacent),
        "non_adjacent_count": len(non_adjacent),
        "non_adjacent_link_ids": sorted(r["link_id"] for r in non_adjacent),
        "peering_locations": peering_locations,
        "distinct_peering_location_count": len(locations),
        "rows": rows,
    }


def _rank_and_pct(country_counts: list[tuple[str, int]]) -> list[dict]:
    """Turn [(country, node_count), ...] into rank (0=most, ties share a
    rank) + pct-of-total, matching Task-2.md's ranking definition."""
    total = sum(c for _, c in country_counts) or 1
    ordered = sorted(country_counts, key=lambda x: -x[1])
    out = []
    rank = 0
    prev_count = None
    for i, (country, count) in enumerate(ordered):
        if prev_count is not None and count < prev_count:
            rank = i
        out.append(
            {"country": country, "node_count": count, "rank": rank, "pct": round(100.0 * count / total, 2)}
        )
        prev_count = count
    return out


# ---------------------------------------------------------------------------
# Task 1 — Level3 <-> Netflix geo-adjacent links
# ---------------------------------------------------------------------------


@envelope("matthewpp.itdk.task1_geo_adjacent_links")
def task1_geo_adjacent_links(as_a: int = 3356, as_b: int = 2906, threshold_km: float = 40.0) -> Result:
    """Find router-level links between two ASes and classify each as
    geographically adjacent or not — the exact query from
    ``nids-itdk-jaber-the-great/Task-1.md`` (default ASes: Level3/Netflix).

    Only links where BOTH endpoints have a geolocation row are considered
    (an inner join on ``itdk_node_geolocation``, matching the notebook's
    query exactly) — a node with no geolocation simply drops its link from
    the result set, it is not reported as a separate "ungeolocated" bucket,
    since that's how the assignment's own query behaves.

    Args:
        as_a: First AS number. Default 3356 (Level3).
        as_b: Second AS number. Default 2906 (Netflix).
        threshold_km: Distance at/under which a link counts as
            geographically adjacent. Default 40.0 (the assignment's
            threshold).

    Returns:
        ``data`` is ``{"adjacent_count": int, "non_adjacent_count": int,
        "non_adjacent_link_ids": [...], "peering_locations": [{"city",
        "country", "link_count"}], "distinct_peering_location_count": int,
        "rows": [{"link_id", "a_node_id", "a_lat", "a_lon", "a_city",
        "a_region", "a_country", "b_node_id", "b_lat", "b_lon", "b_city",
        "b_region", "b_country", "distance_km"}]}``.

    Raises:
        DbConnectionError: the ``caida_itdk`` database is unreachable (live
            mode only).

    Example — the canonical assignment question, answered with one call:

        >>> task1_geo_adjacent_links(3356, 2906, threshold_km=40.0)["data"]["adjacent_count"]
        2
    """
    if is_demo_mode():
        rows = []
        for link_id, nodes in _DEMO_LINK_ENDPOINTS.items():
            a_nodes = [n for n in nodes if _DEMO_NODE_AS.get(n) == as_a and n in _DEMO_NODE_GEO]
            b_nodes = [n for n in nodes if _DEMO_NODE_AS.get(n) == as_b and n in _DEMO_NODE_GEO]
            for a in a_nodes:
                for b in b_nodes:
                    ga, gb = _DEMO_NODE_GEO[a], _DEMO_NODE_GEO[b]
                    rows.append(
                        {
                            "link_id": link_id,
                            "a_node_id": a,
                            "a_lat": ga["latitude"],
                            "a_lon": ga["longitude"],
                            "a_city": ga["city"],
                            "a_region": ga["region"],
                            "a_country": ga["country"],
                            "b_node_id": b,
                            "b_lat": gb["latitude"],
                            "b_lon": gb["longitude"],
                            "b_city": gb["city"],
                            "b_region": gb["region"],
                            "b_country": gb["country"],
                        }
                    )
        warnings = _demo_warning()
    else:
        rows = _run_query(
            """
            WITH a_nodes AS (
                SELECT le.link_id, le.node_id AS a_node_id,
                       g.latitude AS a_lat, g.longitude AS a_lon,
                       g.city AS a_city, g.region AS a_region, g.country AS a_country
                FROM caida_itdk.itdk_link_endpoints le
                JOIN caida_itdk.itdk_node_as na ON le.node_id = na.node_id
                JOIN caida_itdk.itdk_node_geolocation g ON le.node_id = g.node_id
                WHERE na.asn = :as_a
            ),
            b_nodes AS (
                SELECT le.link_id, le.node_id AS b_node_id,
                       g.latitude AS b_lat, g.longitude AS b_lon,
                       g.city AS b_city, g.region AS b_region, g.country AS b_country
                FROM caida_itdk.itdk_link_endpoints le
                JOIN caida_itdk.itdk_node_as na ON le.node_id = na.node_id
                JOIN caida_itdk.itdk_node_geolocation g ON le.node_id = g.node_id
                WHERE na.asn = :as_b
            )
            SELECT a.link_id, a.a_node_id, a.a_lat, a.a_lon, a.a_city, a.a_region, a.a_country,
                   b.b_node_id, b.b_lat, b.b_lon, b.b_city, b.b_region, b.b_country
            FROM a_nodes a
            JOIN b_nodes b ON a.link_id = b.link_id
            """,
            {"as_a": as_a, "as_b": as_b},
        )
        warnings = []

    return Result(data=_classify_geo_rows(rows, threshold_km), warnings=warnings)


# ---------------------------------------------------------------------------
# Task 2 — China Unicom vs. Level3 country footprint + West Coast peering
# ---------------------------------------------------------------------------


@envelope("matthewpp.itdk.task2_country_footprint")
def task2_country_footprint(as_a: int = 4837, as_b: int = 3356) -> Result:
    """Compare two ASes' router footprints by country — the exact query
    from ``nids-itdk-jaber-the-great/Task-2.md`` (default ASes: China
    Unicom vs. Level3).

    Args:
        as_a: First AS number. Default 4837 (China Unicom).
        as_b: Second AS number. Default 3356 (Level3).

    Returns:
        ``data`` is ``{"as_a": {"asn", "total_nodes", "by_country":
        [{"country", "node_count", "rank", "pct"}]}, "as_b": {...}}``.
        ``country`` is the raw ISO 2-letter code — map to a full name
        client-side (e.g. with ``pycountry``) if desired; that's not done
        here to keep this module dependency-light.

    Raises:
        DbConnectionError: the ``caida_itdk`` database is unreachable (live
            mode only).
    """
    if is_demo_mode():
        rows = []
        for asn in (as_a, as_b):
            counts: dict[str, int] = {}
            for node_id, node_asn in _DEMO_NODE_AS.items():
                if node_asn != asn:
                    continue
                geo = _DEMO_NODE_GEO.get(node_id)
                if geo is None:
                    continue
                counts[geo["country"]] = counts.get(geo["country"], 0) + 1
            for country, count in counts.items():
                rows.append({"asn": asn, "country": country, "node_count": count})
        warnings = _demo_warning()
    else:
        rows = _run_query(
            """
            SELECT na.asn, g.country, COUNT(DISTINCT na.node_id) AS node_count
            FROM caida_itdk.itdk_node_as na
            JOIN caida_itdk.itdk_node_geolocation g ON g.node_id = na.node_id
            WHERE na.asn IN (:as_a, :as_b)
            GROUP BY na.asn, g.country
            """,
            {"as_a": as_a, "as_b": as_b},
        )
        warnings = []

    def _for_asn(asn: int) -> dict:
        pairs = [(r["country"], r["node_count"]) for r in rows if r["asn"] == asn]
        return {"asn": asn, "total_nodes": sum(c for _, c in pairs), "by_country": _rank_and_pct(pairs)}

    return Result(data={"as_a": _for_asn(as_a), "as_b": _for_asn(as_b)}, warnings=warnings)


@envelope("matthewpp.itdk.task2_west_coast_peers")
def task2_west_coast_peers(asn: int = 4837, country: str = "US", longitude_max: float = -115.0) -> Result:
    """Identify peer ASes at an AS's routers west of a longitude threshold
    — the exact query from ``nids-itdk-jaber-the-great/Task-2.md`` (default:
    China Unicom's US West Coast routers, longitude < -115 per the
    assignment's threshold).

    Args:
        asn: The AS whose West Coast routers to inspect. Default 4837
            (China Unicom).
        country: ISO 2-letter country filter for the seed routers. Default
            "US".
        longitude_max: Only seed routers west of this longitude (more
            negative = further west). Default -115.0.

    Returns:
        ``data`` is ``{"rows": [{"cu_node", "cu_city", "peer_node",
        "peer_asn", "peer_hostname"}], "distinct_routers_with_peers": int,
        "distinct_peer_asns": int}``. Peers without an AS assignment are
        excluded entirely (inner join, matching the assignment's query);
        ``peer_hostname`` may be ``None`` (no PTR record — a left join, and
        expected).

    Raises:
        DbConnectionError: the ``caida_itdk`` database is unreachable (live
            mode only).
    """
    if is_demo_mode():
        seed_nodes = [
            n
            for n, a in _DEMO_NODE_AS.items()
            if a == asn
            and _DEMO_NODE_GEO.get(n, {}).get("country") == country
            and _DEMO_NODE_GEO.get(n, {}).get("longitude", 0) < longitude_max
        ]
        rows = []
        seen = set()
        for seed in seed_nodes:
            for link_id, nodes in _DEMO_LINK_ENDPOINTS.items():
                if seed not in nodes:
                    continue
                for peer in nodes:
                    if peer == seed:
                        continue
                    peer_asn = _DEMO_NODE_AS.get(peer)
                    if peer_asn is None or peer_asn == asn:
                        continue
                    key = (seed, peer)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "cu_node": seed,
                            "cu_city": _DEMO_NODE_GEO[seed]["city"],
                            "peer_node": peer,
                            "peer_asn": peer_asn,
                            "peer_hostname": _DEMO_HOSTNAMES.get(peer),
                        }
                    )
        warnings = _demo_warning()
    else:
        rows = _run_query(
            """
            WITH cu AS (
                SELECT a.node_id, g.city
                FROM caida_itdk.itdk_node_as a
                JOIN caida_itdk.itdk_node_geolocation g ON g.node_id = a.node_id
                WHERE a.asn = :asn AND g.country = :country AND g.longitude < :longitude_max
            )
            SELECT DISTINCT
                cu.node_id AS cu_node,
                cu.city AS cu_city,
                e2.node_id AS peer_node,
                a2.asn AS peer_asn,
                h.hostname AS peer_hostname
            FROM cu
            JOIN caida_itdk.itdk_link_endpoints e1 ON e1.node_id = cu.node_id
            JOIN caida_itdk.itdk_link_endpoints e2
                ON e2.link_id = e1.link_id
               AND e2.node_id <> cu.node_id
            JOIN caida_itdk.itdk_node_as a2
                ON a2.node_id = e2.node_id
               AND a2.asn <> :asn
            LEFT JOIN caida_itdk.itdk_router_hostnames h
                ON h.ip = NULLIF(split_part(e2.endpoint_token, ':', 2), '')::inet
            """,
            {"asn": asn, "country": country, "longitude_max": longitude_max},
        )
        warnings = []

    data = {
        "rows": rows,
        "distinct_routers_with_peers": len({r["cu_node"] for r in rows}),
        "distinct_peer_asns": len({r["peer_asn"] for r in rows}),
    }
    return Result(data=data, warnings=warnings)


# ---------------------------------------------------------------------------
# Task 3 — 18-AS pairwise router-level link counts
# ---------------------------------------------------------------------------


@envelope("matthewpp.itdk.task3_pairwise_link_counts")
def task3_pairwise_link_counts(asns: list[int] | None = None) -> Result:
    """Count router-level links between every pair of a set of ASes — the
    exact query from ``nids-itdk-jaber-the-great/Task-3.md``. Defaults to
    the assignment's 18 ISPs/CDNs/content ASes (see `ASES`/`AS_CATEGORY`).

    This returns the raw pairwise counts only — the QAP seriation and
    heatmap rendering described in Task-3.md are a downstream, client-side
    analysis/visualization step (``scipy.optimize.quadratic_assignment`` +
    matplotlib), not a database query, so they're intentionally not part of
    this module.

    Args:
        asns: AS numbers to compare pairwise. Defaults to `DEFAULT_18_ASNS`.

    Returns:
        ``data`` is ``{"pairs": [{"asn1", "asn2", "name1", "name2",
        "link_count"}, ...]}``, sorted by ``link_count`` descending. Only
        pairs with at least one observed link appear (a zero-link pair is
        simply absent, matching the assignment's query — it does not
        enumerate the full N×N grid).

    Raises:
        DbConnectionError: the ``caida_itdk`` database is unreachable (live
            mode only).
    """
    asns = asns if asns is not None else DEFAULT_18_ASNS
    asn_set = set(asns)

    if is_demo_mode():
        le_as: set[tuple[str, int]] = set()
        for link_id, nodes in _DEMO_LINK_ENDPOINTS.items():
            for n in nodes:
                a = _DEMO_NODE_AS.get(n)
                if a in asn_set:
                    le_as.add((link_id, a))
        counts: dict[tuple[int, int], int] = {}
        by_link: dict[str, set[int]] = {}
        for link_id, a in le_as:
            by_link.setdefault(link_id, set()).add(a)
        for link_id, as_set in by_link.items():
            ordered = sorted(as_set)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    counts[(a, b)] = counts.get((a, b), 0) + 1
        pairs = [{"asn1": a, "asn2": b, "link_count": c} for (a, b), c in counts.items()]
        warnings = _demo_warning()
    else:
        rows = _run_query(
            """
            WITH le_as AS (
                SELECT DISTINCT le.link_id, na.asn
                FROM caida_itdk.itdk_link_endpoints le
                JOIN caida_itdk.itdk_node_as na ON na.node_id = le.node_id
                WHERE na.asn = ANY(:asns)
            )
            SELECT a.asn AS asn1, b.asn AS asn2, COUNT(*) AS link_count
            FROM le_as a
            JOIN le_as b ON b.link_id = a.link_id AND a.asn < b.asn
            GROUP BY a.asn, b.asn
            ORDER BY link_count DESC
            """,
            {"asns": list(asns)},
        )
        pairs = rows
        warnings = []

    for p in pairs:
        p["name1"] = ASES.get(p["asn1"], str(p["asn1"]))
        p["name2"] = ASES.get(p["asn2"], str(p["asn2"]))
    pairs.sort(key=lambda p: -p["link_count"])
    return Result(data={"pairs": pairs}, warnings=warnings)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="itdk_db", description=__doc__)
    parser.add_argument("--format", choices=["json", "csv", "md"], default="json")
    parser.add_argument("--output-file", default=None, help="write output here instead of stdout")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "task1-geo-adjacent", help="Task 1: geo-adjacent vs. non-adjacent links between two ASes"
    )
    p.add_argument("--as-a", type=int, default=3356, help="default 3356 (Level3)")
    p.add_argument("--as-b", type=int, default=2906, help="default 2906 (Netflix)")
    p.add_argument("--threshold-km", type=float, default=40.0)

    p = sub.add_parser("task2-country-footprint", help="Task 2: router footprint by country for two ASes")
    p.add_argument("--as-a", type=int, default=4837, help="default 4837 (China Unicom)")
    p.add_argument("--as-b", type=int, default=3356, help="default 3356 (Level3)")

    p = sub.add_parser("task2-west-coast-peers", help="Task 2: peer ASes at an AS's West Coast routers")
    p.add_argument("--asn", type=int, default=4837, help="default 4837 (China Unicom)")
    p.add_argument("--country", default="US")
    p.add_argument("--longitude-max", type=float, default=-115.0)

    p = sub.add_parser("task3-pairwise-links", help="Task 3: pairwise link counts across a set of ASes")
    p.add_argument("--asns", help="comma-separated ASN list; defaults to the assignment's 18 ASes")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "task1-geo-adjacent":
        resp = task1_geo_adjacent_links(args.as_a, args.as_b, threshold_km=args.threshold_km)
    elif args.command == "task2-country-footprint":
        resp = task2_country_footprint(args.as_a, args.as_b)
    elif args.command == "task2-west-coast-peers":
        resp = task2_west_coast_peers(args.asn, country=args.country, longitude_max=args.longitude_max)
    elif args.command == "task3-pairwise-links":
        asns = [int(a.strip()) for a in args.asns.split(",")] if args.asns else None
        resp = task3_pairwise_link_counts(asns)
    else:  # pragma: no cover - argparse enforces `choices` above
        parser.error(f"unknown command: {args.command}")
        return 2

    rendered = write_output(resp, path=args.output_file, output_format=args.format)
    if args.output_file is None:
        print(rendered)
    return 0 if resp["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
