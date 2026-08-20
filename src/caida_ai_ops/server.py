"""Single MCP server exposing all of Matthew++'s tools: active Ark measurement
(`ark_measurement.py`), CAIDA AS Rank / customer-cone queries (`as_rank.py`),
and CAIDA ITDK router-level topology queries (`itdk_db.py`).

Replaces the former split servers with one process and one registration. Nothing about the
underlying functions changed: every tool is still a thin wrapper returning
the same JSON output-contract envelope (``{"status", "function",
"parameters", "data", "warnings", "provenance"}``), and every hard limit in
`ark_measurement.py`/`as_rank.py`/`itdk_db.py` still applies unchanged.

Run:
    MATTHEWPP_DEMO=1 python3 mcp_server.py   # synthetic fixtures, no CAIDA access needed
    python3 mcp_server.py                     # live: as_rank works immediately (public data),
                                               # itdk needs db_credentials.env, ark needs scamper/mux access

See README.md's "MCP server" section for how to register this with an MCP
client (Claude Desktop, Claude Code, etc.).
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import ark as m
from . import as_rank as ar
from . import itdk_analysis as db
from .itdk_stdio import register_itdk_tools

mcp = MCPServer(
    name="caida-ai-ops",
    description=(
        "CAIDA AI operations toolkit: active Ark measurement, AS Rank/customer-cone "
        "queries, and ITDK router-level topology queries."
    ),
)

register_itdk_tools(mcp)


def _vp_filter(
    region: str | None,
    country: str | None,
    asn: int | None,
    tag: str | None,
    vp_ids: list[str] | None,
    active_only: bool,
) -> dict:
    return {
        "region": region,
        "country": country,
        "asn": asn,
        "tag": tag,
        "vp_ids": vp_ids,
        "active_only": active_only,
    }


# ---------------------------------------------------------------------------
# Ark measurement — vantage point discovery
# ---------------------------------------------------------------------------


@mcp.tool()
def list_ark_vps(
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    near_lat: float | None = None,
    near_lon: float | None = None,
    radius_km: float = 500.0,
    ipv6_only: bool = False,
    active_only: bool = True,
    limit: int | None = None,
) -> dict:
    """List Ark vantage points (VPs) matching filters. Pass near_lat/near_lon
    together to filter by radius (radius_km) around a point. Use this (or
    nearest_ark_vps) first to discover vp_ids for the measurement tools
    below when you need geo-radius selection rather than region/country/asn/tag."""
    near = (near_lat, near_lon) if near_lat is not None and near_lon is not None else None
    return m.list_vps(
        region=region,
        country=country,
        asn=asn,
        tag=tag,
        near_lat_lon=near,
        radius_km=radius_km,
        ipv6_only=ipv6_only,
        active_only=active_only,
        limit=limit,
    )


@mcp.tool()
def get_ark_vp(vp_id: str) -> dict:
    """Fetch a single Ark VP's metadata by its VP ID."""
    return m.get_vp_by_id(vp_id)


@mcp.tool()
def count_ark_vps(
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    active_only: bool = True,
) -> dict:
    """Count Ark VPs matching filters without fetching full records."""
    return m.count_vps(region=region, country=country, asn=asn, tag=tag, active_only=active_only)


@mcp.tool()
def nearest_ark_vps(lat: float, lon: float, n: int = 5, active_only: bool = True) -> dict:
    """Find the n Ark VPs geographically nearest to a point."""
    return m.nearest_vps(lat, lon, n=n, active_only=active_only)


# ---------------------------------------------------------------------------
# Ark measurement — ping
# ---------------------------------------------------------------------------


@mcp.tool()
def ping_ark(
    target: str,
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    active_only: bool = True,
    duration_s: float = 10.0,
    count: int | None = None,
    interval_ms: int = 1000,
    timeout_ms: int = 2000,
    method: str = "icmp-echo",
    parallel: bool = True,
) -> dict:
    """Ping a target from Ark VPs matching filters. Hard limits: duration_s
    <= 60, count <= 120, interval_ms >= 200, timeout_ms in [100, 10000], at
    most 50 VPs per call — violating any of these returns
    status="error"/MeasurementLimitExceededError rather than clamping."""
    return m.ping(
        target,
        vp_filter=_vp_filter(region, country, asn, tag, vp_ids, active_only),
        duration_s=duration_s,
        count=count,
        interval_ms=interval_ms,
        timeout_ms=timeout_ms,
        method=method,
        parallel=parallel,
    )


@mcp.tool()
def is_ark_reachable(
    target: str,
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    active_only: bool = True,
    timeout_ms: int = 2000,
) -> dict:
    """Single-probe reachability check: is target reachable from the
    matched Ark VPs right now?"""
    return m.is_reachable(
        target, vp_filter=_vp_filter(region, country, asn, tag, vp_ids, active_only), timeout_ms=timeout_ms
    )


@mcp.tool()
def ping_ark_multi_targets(
    targets: list[str],
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    active_only: bool = True,
    duration_s: float = 10.0,
    count: int | None = None,
    method: str = "icmp-echo",
) -> dict:
    """Ping several targets (max 20) from the same set of Ark VPs, grouped
    by target. Same hard limits as ping_ark, applied per target."""
    return m.ping_multi_targets(
        targets,
        vp_filter=_vp_filter(region, country, asn, tag, vp_ids, active_only),
        duration_s=duration_s,
        count=count,
        method=method,
    )


@mcp.tool()
def compare_ark_ping_regions(
    target: str,
    region_a: str,
    region_b: str,
    duration_s: float = 10.0,
    count: int | None = None,
    method: str = "icmp-echo",
) -> dict:
    """Compare average ping latency to a target between two Ark regions."""
    return m.compare_ping_regions(
        target, region_a, region_b, duration_s=duration_s, count=count, method=method
    )


# ---------------------------------------------------------------------------
# Ark measurement — traceroute
# ---------------------------------------------------------------------------


@mcp.tool()
def traceroute_ark(
    target: str,
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    active_only: bool = True,
    method: str = "icmp-paris",
    max_hops: int = 32,
    attempts_per_hop: int = 2,
    wait_ms: int = 1000,
) -> dict:
    """Traceroute to a target from Ark VPs matching filters. Hard limits:
    max_hops <= 64, attempts_per_hop <= 5, wait_ms in [100, 5000], at most
    50 VPs per call. The returned run_id can be passed to
    compare_ark_paths/get_ark_traceroute_run/save_ark_traceroute_baseline
    later."""
    return m.traceroute(
        target,
        vp_filter=_vp_filter(region, country, asn, tag, vp_ids, active_only),
        method=method,
        max_hops=max_hops,
        attempts_per_hop=attempts_per_hop,
        wait_ms=wait_ms,
    )


@mcp.tool()
def get_ark_traceroute_run(run_id: str) -> dict:
    """Fetch a previously stored traceroute run by its run_id (or a label
    registered via save_ark_traceroute_baseline)."""
    return m.get_run(run_id)


@mcp.tool()
def list_ark_traceroute_runs(target: str | None = None, vp_id: str | None = None, limit: int = 20) -> dict:
    """List recent stored traceroute runs, optionally filtered by target or VP."""
    return m.list_runs(target=target, vp_id=vp_id, limit=limit)


@mcp.tool()
def save_ark_traceroute_baseline(run_id: str, label: str) -> dict:
    """Label a stored traceroute run (e.g. "pre-incident") so later compare
    calls can reference the label instead of the raw run_id."""
    return m.save_run_as_baseline(run_id, label)


@mcp.tool()
def compare_ark_paths(
    target: str,
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    active_only: bool = True,
    baseline_run_id: str | None = None,
    flag_transit_asn: int | None = None,
    flag_transit_ip_prefix: str | None = None,
) -> dict:
    """Run a fresh traceroute and diff it against a prior run (baseline_run_id)
    and/or flag transit through a suspect AS/IP prefix — the direct tool
    for "is traffic detouring through X?" questions."""
    return m.compare_paths(
        target,
        vp_filter=_vp_filter(region, country, asn, tag, vp_ids, active_only),
        baseline_run_id=baseline_run_id,
        flag_transit_asn=flag_transit_asn,
        flag_transit_ip_prefix=flag_transit_ip_prefix,
    )


@mcp.tool()
def check_ark_transit(
    target: str,
    asn: int | None = None,
    ip_prefix: str | None = None,
    region: str | None = None,
    country: str | None = None,
    vp_asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    active_only: bool = True,
) -> dict:
    """One-shot check: does the current path from matched VPs to target
    transit a suspect AS or IP prefix? (No historical baseline needed —
    use compare_ark_paths for that.)"""
    return m.check_transit(
        target,
        asn=asn,
        ip_prefix=ip_prefix,
        vp_filter=_vp_filter(region, country, vp_asn, tag, vp_ids, active_only),
    )


@mcp.tool()
def list_ark_hops_by_asn(run_id: str, asn: int) -> dict:
    """Within a stored traceroute run, list every hop belonging to a given AS."""
    return m.list_hops_by_asn(run_id, asn)


# ---------------------------------------------------------------------------
# Ark measurement — DNS
# ---------------------------------------------------------------------------


@mcp.tool()
def dns_query_ark(
    qname: str,
    qtype: str = "A",
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    active_only: bool = True,
    resolver: str = "system",
    timeout_ms: int = 2000,
) -> dict:
    """Query DNS from Ark VPs matching filters. qtype is one of A, AAAA,
    NS, MX, TXT, DS, DNSKEY, CAA, SOA."""
    return m.dns_query(
        qname,
        qtype=qtype,
        vp_filter=_vp_filter(region, country, asn, tag, vp_ids, active_only),
        resolver=resolver,
        timeout_ms=timeout_ms,
    )


@mcp.tool()
def dns_divergence_report_ark(
    qname: str,
    qtype: str = "A",
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    active_only: bool = True,
) -> dict:
    """Does the DNS answer for qname differ depending on which Ark VP asks?
    Groups answers by distinct answer set (majority vs. minority VPs) —
    use this instead of dns_query_ark when the question is about divergence."""
    return m.dns_divergence_report(
        qname, qtype=qtype, vp_filter=_vp_filter(region, country, asn, tag, vp_ids, active_only)
    )


@mcp.tool()
def check_ark_dnssec_valid(
    qname: str,
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    active_only: bool = True,
) -> dict:
    """Simplified per-VP DNSSEC presence check (DS + DNSKEY both present).
    A presence heuristic, not full chain-of-trust validation."""
    return m.check_dnssec_valid(qname, vp_filter=_vp_filter(region, country, asn, tag, vp_ids, active_only))


# ---------------------------------------------------------------------------
# Ark measurement — multi-vantage-point root-cause diagnosis
# ---------------------------------------------------------------------------


@mcp.tool()
def diagnose_ark_path_anomaly(
    target: str,
    suspect_asn: int | None = None,
    suspect_ip_prefix: str | None = None,
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    vp_ids: list[str] | None = None,
    active_only: bool = True,
    sample_size: int = 15,
    include_bgp_context: bool = True,
) -> dict:
    """Multi-VP diagnostic for a suspected routing anomaly (e.g. "is traffic
    to X detouring through Y, and is it global or regional?"). Samples up
    to sample_size VPs (hard max 50), checks transit through suspect_asn/
    suspect_ip_prefix on each, and returns evidence (percent affected,
    affected regions/VPs) — deliberately not a verdict; reason over the
    evidence yourself rather than treating the output as a root-cause
    conclusion."""
    return m.diagnose_path_anomaly(
        target,
        suspect_asn=suspect_asn,
        suspect_ip_prefix=suspect_ip_prefix,
        vp_filter=_vp_filter(region, country, asn, tag, vp_ids, active_only),
        sample_size=sample_size,
        include_bgp_context=include_bgp_context,
    )


# ---------------------------------------------------------------------------
# AS Rank / customer cone (nids-asn-introduction-jaber-the-great) — see as_rank.py
# ---------------------------------------------------------------------------


@mcp.tool()
def as_rank_download_datasets(dataset_date: str = ar.DEFAULT_DATASET_DATE, force: bool = False) -> dict:
    """Download (or reuse a cached copy of) CAIDA's public AS Customer
    Cone + AS-to-Organization datasets. Called automatically by every
    other as_rank_* tool on first use -- call this directly only to
    pre-warm the cache or force a refresh with `force=True`."""
    return ar.download_as_rank_datasets(dataset_date, force=force)


@mcp.tool()
def as_rank_get_customer_cone(asn: int, dataset_date: str = ar.DEFAULT_DATASET_DATE) -> dict:
    """Look up one AS's customer-cone size and tier (edge / transit
    small / transit middle / transit large / transit huge)."""
    return ar.get_customer_cone(asn, dataset_date=dataset_date)


@mcp.tool()
def as_rank_resolve_org(asn: int, dataset_date: str = ar.DEFAULT_DATASET_DATE) -> dict:
    """Look up the organization and country CAIDA's AS2Org mapping
    associates with an AS."""
    return ar.resolve_asn_org(asn, dataset_date=dataset_date)


@mcp.tool()
def as_rank_tier_distribution(dataset_date: str = ar.DEFAULT_DATASET_DATE) -> dict:
    """Task 2: tier distribution across all ASNs -- answers "what
    percentage of ASNs are edge ASes?", "how large is the max customer
    cone?", and "how do tier proportions compare?" directly."""
    return ar.cone_tier_distribution(dataset_date=dataset_date)


@mcp.tool()
def as_rank_country_breakdown(dataset_date: str = ar.DEFAULT_DATASET_DATE, top_n_countries: int = 4) -> dict:
    """Task 3: tier x country breakdown -- answers "which countries have
    the most transit-huge ASNs?" and "do the same countries dominate every
    tier?" directly."""
    return ar.tier_country_breakdown(dataset_date=dataset_date, top_n_countries=top_n_countries)


@mcp.tool()
def as_rank_list_by_tier(
    tier: str, dataset_date: str = ar.DEFAULT_DATASET_DATE, limit: int = ar.DEFAULT_LIST_LIMIT
) -> dict:
    """List ASNs in a given tier, largest cone first. tier is one of
    "edge", "transit small", "transit middle", "transit large",
    "transit huge". Hard-capped at 2000 results."""
    return ar.list_asns_by_tier(tier, dataset_date=dataset_date, limit=limit)


@mcp.tool()
def as_rank_top_cones(n: int = 10, dataset_date: str = ar.DEFAULT_DATASET_DATE) -> dict:
    """The N ASes with the largest customer cones (most influential, by
    this metric), with organization/country attached."""
    return ar.largest_customer_cones(n, dataset_date=dataset_date)


# ---------------------------------------------------------------------------
# ITDK (nids-itdk-jaber-the-great) — see itdk_db.py
# ---------------------------------------------------------------------------


@mcp.tool()
def itdk_geo_adjacent_links(as_a: int = 3356, as_b: int = 2906, threshold_km: float = 40.0) -> dict:
    """Task 1: classify router-level links between two ASes as
    geographically adjacent or not (default: Level3 vs. Netflix, the
    canonical assignment question). Requires live caida_itdk DB access
    (or MATTHEWPP_DEMO=1)."""
    return db.task1_geo_adjacent_links(as_a, as_b, threshold_km=threshold_km)


@mcp.tool()
def itdk_country_footprint(as_a: int = 4837, as_b: int = 3356) -> dict:
    """Task 2: compare two ASes' router footprints by country (default:
    China Unicom vs. Level3)."""
    return db.task2_country_footprint(as_a, as_b)


@mcp.tool()
def itdk_west_coast_peers(asn: int = 4837, country: str = "US", longitude_max: float = -115.0) -> dict:
    """Task 2: identify peer ASes at an AS's routers west of a longitude
    threshold (default: China Unicom's US West Coast routers)."""
    return db.task2_west_coast_peers(asn, country=country, longitude_max=longitude_max)


@mcp.tool()
def itdk_pairwise_link_counts(asns: list[int] | None = None) -> dict:
    """Task 3: router-level link counts between every pair of a set of
    ASes. Defaults to the assignment's 18 ISPs/CDNs/content ASes."""
    return db.task3_pairwise_link_counts(asns)


# ---------------------------------------------------------------------------
# Durable Ark results and IP metadata
# ---------------------------------------------------------------------------


@mcp.tool()
def list_ark_results(
    function: str | None = None,
    target: str | None = None,
    backend: str | None = None,
    limit: int = 20,
) -> dict:
    """List archived measurements, newest first, with optional filters."""
    return m.list_results(function=function, target=target, backend=backend, limit=limit)


@mcp.tool()
def get_ark_result(result_id: str) -> dict:
    """Fetch a complete archived measurement by result ID."""
    return m.get_result(result_id)


@mcp.tool()
def export_ark_result(
    result_id: str,
    output_format: str = "csv",
    output_file: str | None = None,
) -> dict:
    """Export an archived measurement as CSV, Markdown, or JSON."""
    return m.export_result(result_id, output_format=output_format, output_file=output_file)


@mcp.tool()
def find_ips_in_city(city: str, country: str | None = None, limit: int = 10) -> dict:
    """Find RFC 8805 geofeed candidate IPs for a city."""
    return m.find_ips_in_city(city, country=country, limit=limit)


@mcp.tool()
def lookup_ip_metadata(ip: str) -> dict:
    """Look up sourced ASN, declared location, and registry metadata for an IP."""
    return m.lookup_ip(ip)


@mcp.tool()
def enrich_ark_result(result_id: str) -> dict:
    """Add sourced per-hop ASN and geolocation fields to a stored result."""
    return m.enrich_result(result_id)


def main() -> None:
    """Run the complete unified server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
