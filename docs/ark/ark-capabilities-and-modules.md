# Ark Natural-Language Capability Catalog

Companion to [`matthew-plus-plus.md`](./matthew-plus-plus.md). That document lays out the
principle: don't ask an LLM to improvise measurement code, give it a fixed, deterministic,
well-documented set of functions and let it do parameter extraction only. This document is the
concrete catalog — organized by capability, each with example natural-language questions a user
could ask, and a fully specified module the agent calls (never writes) to answer them.

## Design contract

Every capability below follows the same shape, so the agent's job is always identical: parse
intent → fill in a function signature → call it → hand the structured result back in prose.

1. **One Python function, fully typed, fully docstringed** (Google style: `Args` / `Returns` /
   `Raises` / `Example`) — this is what the agent calls directly via MCP tool-calling.
2. **One CLI wrapper** exposing the same parameters as flags — for a human, for testing, and as
   a second, redundant interface the agent can shell out to if the MCP tool layer is unavailable.
3. **One deterministic output contract** (see [Appendix](#appendix-shared-output-contract)) — same
   JSON shape every time, so the agent never has to write ad hoc parsing code for a new answer
   shape.

Every module lives under a proposed `matthewpp/` package (`matthewpp.ark.*` for live Ark
measurement, `matthewpp.itdk.*` for topology-dataset queries, etc.) — names below are proposals
for the hackathon build-out, not existing code.

---

## Category 1 — Vantage Point Discovery

Ark is only useful if you can find the right probing points first. This capability answers "where
can I measure from," which is usually the first step in any other question below.

**Example questions:**
- "How many Ark vantage points are currently active in Africa?"
- "List all Ark VPs hosted in AS6939 (Hurricane Electric)."
- "Which Ark VPs in South America support IPv6?"
- "Find Ark VPs within 500 km of Cape Town."
- "Is there an active Ark VP in Iran right now?"

### Module: `matthewpp.ark.vps.list_vps`

```python
def list_vps(
    region: str | None = None,
    country: str | None = None,
    asn: int | None = None,
    tag: str | None = None,
    near_lat_lon: tuple[float, float] | None = None,
    radius_km: float = 500.0,
    ipv6_only: bool = False,
    active_only: bool = True,
    limit: int | None = None,
    output_format: str = "json",
) -> dict:
    """List Ark vantage points (VPs) matching the given filters.

    Wraps `ScamperCtrl.vps()` and filters/sorts the resulting VP metadata
    (region, country, ASN, tags, geo-coordinates, IPv4/IPv6 capability,
    last-seen heartbeat) without requiring the caller to know the
    underlying scamper mux protocol.

    Args:
        region: Free-text macro-region filter, e.g. "africa", "east-asia",
            "south-america". Matched against each VP's region tag.
        country: ISO 3166-1 alpha-2 country code, e.g. "ZA", "IR". Takes
            precedence over `region` if both given.
        asn: Filter to VPs whose hosting network is this AS number.
        tag: Arbitrary Ark VP tag (e.g. "ipv6", "residential", "cloud") to
            require.
        near_lat_lon: `(lat, lon)` in decimal degrees; if set, only VPs
            within `radius_km` of this point are returned, sorted by
            distance ascending.
        radius_km: Radius in kilometers used with `near_lat_lon`. Ignored
            if `near_lat_lon` is None. Default 500.0.
        ipv6_only: If True, only return VPs with IPv6 connectivity.
        active_only: If True (default), exclude VPs whose last heartbeat
            is older than the Ark mux staleness threshold.
        limit: Cap the number of VPs returned. None = no cap.
        output_format: "json" (default, structured dict), "csv", or
            "md" (markdown table) — controls only the `data` field's
            serialization in the return value.

    Returns:
        Output-contract dict (see Appendix) whose `data` field is a list
        of VP records: `{vp_id, hostname, asn, org, country, region,
        lat, lon, ipv4, ipv6, tags, last_heartbeat}`.

    Raises:
        ArkMuxUnavailableError: if the Ark mux socket cannot be reached.
        InvalidFilterError: if `country` is not a valid ISO alpha-2 code,
            or `near_lat_lon` is set without a usable coordinate pair.

    Example:
        >>> list_vps(region="africa", active_only=True)
        {"status": "ok", "data": [{"vp_id": "ark-jnb-za", "country": "ZA", ...}, ...]}
    """
```

**CLI:** `ark-vps`

| Flag | Type | Default | Description |
|---|---|---|---|
| `--region` | str | None | Macro-region filter |
| `--country` | str (ISO-2) | None | Country filter, overrides `--region` |
| `--asn` | int | None | Hosting AS filter |
| `--tag` | str | None | Ark VP tag filter |
| `--near` | `"lat,lon"` | None | Center point for radius search |
| `--radius-km` | float | 500.0 | Radius for `--near` |
| `--ipv6-only` | flag | off | Require IPv6 capability |
| `--active-only` / `--include-inactive` | flag | active-only | Heartbeat freshness filter |
| `--limit` | int | None | Max VPs returned |
| `--format` | `{json,csv,md}` | json | Output serialization |

**NL → call:** *"Which Ark VPs in South America support IPv6?"* →
`list_vps(region="south-america", ipv6_only=True)`

---

## Category 2 — Active Reachability & Latency (Ping)

**Example questions:**
- "Take the nodes in Africa and ping google.com for 10 seconds."
- "Is 8.8.8.8 reachable from Ark VPs in East Asia right now?"
- "Compare average latency to cloudflare.com from US vs. European Ark VPs."
- "Run 20 pings to 1.1.1.1 from 5 VPs near Frankfurt and report packet loss."

### Module: `matthewpp.ark.ping.ping`

```python
def ping(
    target: str,
    vp_filter: dict | None = None,
    duration_s: float | None = 10.0,
    count: int | None = None,
    interval_ms: int = 1000,
    timeout_ms: int = 2000,
    method: str = "icmp-echo",
    parallel: bool = True,
    output_format: str = "json",
) -> dict:
    """Ping a target from one or more Ark vantage points.

    Wraps `ScamperCtrl.do_ping()`. Exactly one of `duration_s` or `count`
    determines when probing to a given VP stops; if both are set,
    whichever limit is reached first wins.

    Args:
        target: Hostname or IP address to ping. Hostnames are resolved
            once, centrally, before dispatch (not independently per VP),
            so all VPs probe the same IP unless `resolve_per_vp` is
            later added as a capability (see Roadmap).
        vp_filter: Same filter dict accepted by `list_vps` (`region`,
            `country`, `asn`, `tag`, `near_lat_lon`, `radius_km`,
            `ipv6_only`). None = use every active Ark VP (expensive;
            a warning is included in the response if unfiltered).
        duration_s: Wall-clock seconds to probe from each VP. Default 10.
        count: Fixed probe count per VP; overrides `duration_s` if set.
        interval_ms: Milliseconds between probes from a single VP.
        timeout_ms: Per-probe reply timeout in milliseconds.
        method: One of "icmp-echo", "icmp-time", "tcp-syn", "udp".
        parallel: If True (default), dispatch to all matched VPs
            concurrently; if False, probe sequentially (slower, gentler
            on shared Ark capacity).
        output_format: "json", "csv", or "md".

    Returns:
        Output-contract dict; `data` is a list of per-VP results:
        `{vp_id, target_ip, sent, received, loss_pct, rtt_min_ms,
        rtt_avg_ms, rtt_max_ms, rtt_stddev_ms}`.

    Raises:
        ArkMuxUnavailableError: mux socket unreachable.
        NoMatchingVPsError: `vp_filter` matched zero active VPs.
        UnsupportedMethodError: `method` not one of the supported values.

    Example:
        >>> ping("google.com", vp_filter={"region": "africa"}, duration_s=10)
        {"status": "ok", "data": [{"vp_id": "ark-lag-ng", "rtt_avg_ms": 42.1, ...}]}
    """
```

**CLI:** `ark-ping`

| Flag | Type | Default | Description |
|---|---|---|---|
| `--target` | str | required | Hostname/IP to ping |
| `--region` / `--country` / `--asn` / `--tag` | str | None | VP filters (as in `ark-vps`) |
| `--near` / `--radius-km` | str / float | None / 500.0 | Radius-based VP filter |
| `--duration-s` | float | 10.0 | Probe duration per VP |
| `--count` | int | None | Fixed probe count (overrides duration) |
| `--interval-ms` | int | 1000 | Inter-probe spacing |
| `--timeout-ms` | int | 2000 | Reply timeout |
| `--method` | `{icmp-echo,icmp-time,tcp-syn,udp}` | icmp-echo | Probe method |
| `--sequential` | flag | off (parallel) | Disable concurrent dispatch |
| `--format` | `{json,csv,md}` | json | Output serialization |

**NL → call:** *"Take the nodes in Africa and ping google.com for 10 seconds"* →
`ping(target="google.com", vp_filter={"region": "africa"}, duration_s=10)`

---

## Category 3 — Path Measurement & Route-Anomaly Detection (Traceroute)

**Example questions:**
- "Traceroute from 5 European Ark VPs to example.com and show the paths."
- "Does the path to 203.0.113.5 from VPs near Singapore pass through AS7018?"
- "Compare today's path to my-service.com against the traceroute I ran last Tuesday."
- "Is traffic to `dest-ip` detouring through San Diego before reaching its destination, and is that happening from every region or just one?"

### Module: `matthewpp.ark.traceroute.traceroute`

```python
def traceroute(
    target: str,
    vp_filter: dict | None = None,
    method: str = "icmp-paris",
    max_hops: int = 32,
    attempts_per_hop: int = 2,
    wait_ms: int = 1000,
    output_format: str = "json",
) -> dict:
    """Run traceroute(s) to a target from one or more Ark vantage points.

    Wraps `ScamperCtrl.do_traceroute()`.

    Args:
        target: Hostname or IP address to trace to.
        vp_filter: Same filter dict as `list_vps`/`ping`. None = all
            active VPs (a cost warning is included in the response).
        method: Traceroute variant, one of "icmp-paris", "udp-paris",
            "tcp".
        max_hops: Maximum TTL to probe before giving up.
        attempts_per_hop: Probes per hop before marking it non-responsive.
        wait_ms: Per-probe reply timeout in milliseconds.
        output_format: "json", "csv", or "md".

    Returns:
        Output-contract dict; `data` is a list of per-VP traces:
        `{vp_id, target_ip, hops: [{ttl, ip, asn, rtt_ms}], reached,
        run_id, timestamp}`. Every trace is stamped with a `run_id` so it
        can be referenced later by `compare_paths` or archived as a
        baseline.

    Raises:
        ArkMuxUnavailableError, NoMatchingVPsError: as in `ping`.

    Example:
        >>> traceroute("example.com", vp_filter={"region": "europe"}, max_hops=20)
        {"status": "ok", "data": [{"vp_id": "ark-fra-de", "hops": [...], "run_id": "tr_20260819_001"}]}
    """


def compare_paths(
    target: str,
    vp_filter: dict | None = None,
    baseline_run_id: str | None = None,
    flag_transit_asn: int | None = None,
    flag_transit_ip_prefix: str | None = None,
    output_format: str = "json",
) -> dict:
    """Run a fresh traceroute and compare it against a prior run or a
    flagged AS/prefix, to detect route anomalies (unexpected detours,
    added/removed hops, path-length changes).

    This is the direct tool for questions like "is traffic detouring
    through <place>?" — pass the suspect AS or prefix in
    `flag_transit_asn` / `flag_transit_ip_prefix` and this function
    reports, per VP, whether the current path transits it.

    Args:
        target: Hostname or IP address to trace to.
        vp_filter: Same filter dict as `traceroute`.
        baseline_run_id: A `run_id` from a previous `traceroute()` call
            to diff against. If None, only the flag checks below run
            (no historical diff).
        flag_transit_asn: If set, flag any current path whose hop list
            includes this AS number.
        flag_transit_ip_prefix: If set (CIDR notation), flag any current
            path with a hop IP inside this prefix.
        output_format: "json", "csv", or "md".

    Returns:
        Output-contract dict; `data` is a list of per-VP comparison
        records: `{vp_id, current_run_id, baseline_run_id,
        path_changed: bool, hops_added, hops_removed,
        transits_flagged_asn: bool, transits_flagged_prefix: bool}`.

    Raises:
        ArkMuxUnavailableError, NoMatchingVPsError: as in `ping`.
        UnknownRunIdError: `baseline_run_id` not found in the run store.

    Example:
        >>> compare_paths("dest.example.com", flag_transit_asn=7377)  # UCSD/CAIDA ASN, illustrative
        {"status": "ok", "data": [{"vp_id": "ark-nrt-jp", "transits_flagged_asn": True, ...}]}
    """
```

**CLI:** `ark-traceroute` / `ark-traceroute-compare`

| Flag | Type | Default | Description |
|---|---|---|---|
| `--target` | str | required | Trace destination |
| `--region` / `--country` / `--asn` / `--tag` / `--near` / `--radius-km` | — | None | VP filters |
| `--method` | `{icmp-paris,udp-paris,tcp}` | icmp-paris | Traceroute variant |
| `--max-hops` | int | 32 | TTL ceiling |
| `--attempts-per-hop` | int | 2 | Retries per hop |
| `--wait-ms` | int | 1000 | Reply timeout |
| `--baseline-run-id` | str | None | (compare only) prior run to diff against |
| `--flag-transit-asn` | int | None | (compare only) AS to flag if transited |
| `--flag-transit-prefix` | CIDR | None | (compare only) prefix to flag if transited |
| `--format` | `{json,csv,md}` | json | Output serialization |

**NL → call:** *"Is traffic to 203.0.113.5 detouring through San Diego?"* →
`compare_paths(target="203.0.113.5", flag_transit_asn=<CAIDA/UCSD ASN>)`

---

## Category 4 — DNS Divergence Measurement

**Example questions:**
- "Resolve the A record for example.com from 20 Ark VPs across different regions — does the answer differ by location?"
- "What authoritative nameservers does example.com return from VPs in Brazil vs. Japan?"
- "Check whether a domain's DNSSEC validation passes from multiple vantage points."

### Module: `matthewpp.ark.dns.query`

```python
def query(
    qname: str,
    qtype: str = "A",
    vp_filter: dict | None = None,
    resolver: str = "system",
    timeout_ms: int = 2000,
    output_format: str = "json",
) -> dict:
    """Issue a DNS query from one or more Ark vantage points and report
    per-VP answers, to detect geo/anycast-driven divergence.

    Wraps scamper's DNS-query capability (`do_dns` alongside
    `do_ping`/`do_traceroute` in `ScamperCtrl`).

    Args:
        qname: Domain name to query.
        qtype: DNS record type: "A", "AAAA", "NS", "MX", "TXT", "DS",
            "DNSKEY", "CAA", "SOA".
        vp_filter: Same filter dict as `list_vps`.
        resolver: "system" (each VP's local resolver, default — best for
            detecting real-world geo/anycast divergence) or a specific
            resolver IP to query uniformly from every VP instead.
        timeout_ms: Per-query reply timeout in milliseconds.
        output_format: "json", "csv", or "md".

    Returns:
        Output-contract dict; `data` is a list of per-VP answers:
        `{vp_id, resolver_used, answers: [...], rcode, rtt_ms}`, plus a
        top-level `divergence` summary: `{unique_answer_sets: int,
        majority_answer: [...], minority_vps: [...]}`.

    Raises:
        ArkMuxUnavailableError, NoMatchingVPsError: as in `ping`.
        UnsupportedQtypeError: `qtype` not in the supported list.

    Example:
        >>> query("example.com", qtype="A", vp_filter={"region": "global-sample"})
        {"status": "ok", "data": [...], "divergence": {"unique_answer_sets": 3, ...}}
    """
```

**CLI:** `ark-dns`

| Flag | Type | Default | Description |
|---|---|---|---|
| `--qname` | str | required | Domain to query |
| `--qtype` | `{A,AAAA,NS,MX,TXT,DS,DNSKEY,CAA,SOA}` | A | Record type |
| `--region` / `--country` / `--asn` / `--tag` / `--near` / `--radius-km` | — | None | VP filters |
| `--resolver` | `"system"` or IP | system | Resolver to use |
| `--timeout-ms` | int | 2000 | Reply timeout |
| `--format` | `{json,csv,md}` | json | Output serialization |

**NL → call:** *"Do DNS answers for example.com differ between Brazil and Japan?"* →
`query(qname="example.com", vp_filter={"country": "BR"})` and
`query(qname="example.com", vp_filter={"country": "JP"})`, diffed by the agent from the two
`data` lists (no new code — just comparing two structured results).

---

## Category 5 — Multi-Vantage-Point Root-Cause Diagnosis

This is the orchestration layer `matthew-plus-plus.md` is really about: the SDSC-detour incident
needed traceroutes from several regions *plus* BGP context, correlated by a human. This module
runs the multi-tool part deterministically; only the final synthesis is left to the model.

**Example questions:**
- "Traffic to `dest-ip` seems to be routing through San Diego unexpectedly — figure out why, and whether it's a global or regional problem."
- "Diagnose why latency to our service spiked for users in Southeast Asia yesterday."
- "Is a reported outage in country X visible in Ark's traceroute data?"

### Module: `matthewpp.ark.diagnose.diagnose_path_anomaly`

```python
def diagnose_path_anomaly(
    target: str,
    suspect_asn: int | None = None,
    suspect_ip_prefix: str | None = None,
    vp_filter: dict | None = None,
    sample_size: int = 15,
    include_bgp_context: bool = True,
    output_format: str = "json",
) -> dict:
    """Run a scoped multi-VP diagnostic for a suspected routing anomaly.

    Orchestrates, in order: (1) `list_vps` to pick a geographically
    diverse sample, (2) `compare_paths` from each sampled VP to `target`,
    flagging transit through `suspect_asn`/`suspect_ip_prefix`, and (3),
    if `include_bgp_context` is True, a best-effort cross-reference
    against BGP table data for the ASes seen adjacent to the flagged hop
    (see Roadmap — this step degrades to a `"bgp_context": "unavailable"`
    stub until the BGP module below is built, rather than failing the
    whole call).

    This function does not draw a root-cause conclusion — per the
    "attribution without overclaiming" principle, it returns the
    evidence (which VPs saw the detour, how widespread it is, what BGP
    shows for the relevant ASes) for the agent to reason over in
    natural language, not a pre-baked verdict.

    Args:
        target: Hostname or IP suspected of anomalous routing.
        suspect_asn: AS number to check for unexpected transit.
        suspect_ip_prefix: CIDR prefix to check for unexpected transit.
        vp_filter: Restrict the diagnostic sample to a subset of VPs
            (e.g. `{"region": "southeast-asia"}`); None = draw a
            globally diverse sample of `sample_size` VPs.
        sample_size: Number of VPs to sample when `vp_filter` doesn't
            already narrow the pool to a small set.
        include_bgp_context: Whether to attempt the BGP cross-reference
            step (see Roadmap).
        output_format: "json", "csv", or "md".

    Returns:
        Output-contract dict; `data` contains `{per_vp_results: [...],
        pct_vps_affected, affected_regions: [...], bgp_context: {...} |
        "unavailable"}`.

    Raises:
        ArkMuxUnavailableError, NoMatchingVPsError: as in `ping`.

    Example:
        >>> diagnose_path_anomaly("dest.example.com", suspect_asn=<CAIDA/UCSD ASN>)
        {"status": "ok", "data": {"pct_vps_affected": 0.31, "affected_regions": ["east-asia"], ...}}
    """
```

**CLI:** `ark-diagnose`

| Flag | Type | Default | Description |
|---|---|---|---|
| `--target` | str | required | Suspect destination |
| `--suspect-asn` | int | None | AS to check for unexpected transit |
| `--suspect-prefix` | CIDR | None | Prefix to check for unexpected transit |
| `--region` / `--country` / `--asn` / `--tag` | — | None | Restrict VP sample |
| `--sample-size` | int | 15 | VP sample size if unfiltered |
| `--no-bgp-context` | flag | off (BGP context on) | Skip the BGP cross-reference step |
| `--format` | `{json,csv,md}` | json | Output serialization |

---

## Category 6 — Topology & Geolocation Analysis (ITDK)

Not a live Ark probe — a query over CAIDA's **Internet Topology Data Kit**, the pre-built
router-level topology *derived from* Ark's traceroute campaigns (see
`nids-itdk-jaber-the-great/CLAUDE.md`). Included here because it answers the same shape of
question ("what does the topology between these two networks look like") without spinning up new
measurements, and is exactly the kind of dataset the bigger Matthew++ vision wants wrapped the
same deterministic way.

**Example questions:**
- "How many router-level links between Level3 and Netflix were inferred to be geographically adjacent (within 40 km)? How many were not? Report the link IDs of the non-adjacent ones."
- "What's the largest inter-AS distance among inferred links between AS X and AS Y?"
- "List all ITDK nodes assigned to Level3 that have no geolocation."

### Module: `matthewpp.itdk.topology.geo_adjacency_between_orgs`

```python
def geo_adjacency_between_orgs(
    org_a: str,
    org_b: str,
    threshold_km: float = 40.0,
    require_both_geolocated: bool = True,
    db_dsn_env_var: str = "ITDK_DB_DSN",
    output_format: str = "json",
) -> dict:
    """Classify inferred router-level links between two organizations'
    ASes as geographically adjacent or not, using ITDK's node
    geolocation and link tables.

    For every ITDK link with at least one endpoint node owned by an AS
    belonging to `org_a` and at least one endpoint node owned by an AS
    belonging to `org_b`, this looks up each endpoint's geolocation,
    computes the great-circle (haversine) distance between them, and
    buckets the link as adjacent (`distance_km <= threshold_km`) or not.

    Org → AS resolution uses the same CAIDA AS2Org mapping used in
    `nids-asn-introduction`/`nids-bgp-control-plane` (matched by
    `orgName`, case-insensitive substring match, e.g. "Level 3" /
    "Level3 Parent, LLC" both resolve to the same org).

    Note on multi-access links: an ITDK link can join more than two
    nodes (a shared-medium adjacency, not just a point-to-point one).
    For a link with more than one node per side, this function evaluates
    every A-side/B-side node pair on that link and reports the link as
    adjacent only if *every* such pair is within `threshold_km` — a
    conservative choice, called out explicitly in the returned record
    (`pairwise_distances_km`) rather than silently collapsing it.

    Args:
        org_a: Organization name or alias (e.g. "Level3", "Level 3
            Communications"). Resolved via AS2Org substring match.
        org_b: Second organization name or alias (e.g. "Netflix").
        threshold_km: Distance in kilometers at or under which a link
            counts as geographically adjacent. Default 40.0, matching
            the standard NIDS-ITDK assignment threshold.
        require_both_geolocated: If True (default), links where either
            endpoint has no geolocation are excluded from both counts
            and reported separately as `ungeolocated_link_ids` rather
            than silently counted as non-adjacent.
        db_dsn_env_var: Name of the environment variable holding the
            Postgres connection string for the `caida_itdk` database
            (never the literal DSN — this function reads it from the
            environment, consistent with how `db_credentials.env` is
            handled elsewhere in this workspace).
        output_format: "json", "csv", or "md".

    Returns:
        Output-contract dict; `data` is:
        `{org_a_resolved, org_b_resolved, org_a_asns, org_b_asns,
        adjacent_count, non_adjacent_count, ungeolocated_count,
        non_adjacent_link_ids: [...], ungeolocated_link_ids: [...],
        distance_stats: {min_km, max_km, median_km}}`.

    Raises:
        OrgNotFoundError: `org_a` or `org_b` matches zero ASes in AS2Org.
        DbConnectionError: `db_dsn_env_var` unset or connection fails.

    Example:
        >>> geo_adjacency_between_orgs("Level3", "Netflix", threshold_km=40.0)
        {
          "status": "ok",
          "data": {
            "org_a_resolved": "Level 3 Parent, LLC",
            "org_b_resolved": "Netflix, Inc.",
            "adjacent_count": 118,
            "non_adjacent_count": 7,
            "ungeolocated_count": 3,
            "non_adjacent_link_ids": ["L48213", "L91007", "L102284", "..."],
            "distance_stats": {"min_km": 0.4, "max_km": 3112.7, "median_km": 6.1}
          }
        }
    """
```

**CLI:** `itdk-geo-adjacency`

| Flag | Type | Default | Description |
|---|---|---|---|
| `--org-a` | str | required | First organization name/alias |
| `--org-b` | str | required | Second organization name/alias |
| `--threshold-km` | float | 40.0 | Adjacency distance cutoff |
| `--allow-ungeolocated` | flag | off (excluded) | Count ungeolocated-endpoint links as non-adjacent instead of excluding them |
| `--db-dsn-env-var` | str | `ITDK_DB_DSN` | Env var holding the DB connection string |
| `--format` | `{json,csv,md}` | json | Output serialization |
| `--limit-ids` | int | None | Cap how many non-adjacent link IDs are printed (full list still in JSON) |

**NL → call — worked end to end**, matching the ITDK assignment question verbatim:

> *"How many router-level links between Level3 and Netflix were inferred to be geographically
> adjacent (within 40 km of each other)? How many were not? Report the link IDs of the
> non-adjacent links."*

```
geo_adjacency_between_orgs(org_a="Level3", org_b="Netflix", threshold_km=40.0)
```

The agent extracts exactly three parameters from the sentence (`org_a`, `org_b`, `threshold_km`),
calls the function once, and reads the answer straight off `data.adjacent_count`,
`data.non_adjacent_count`, and `data.non_adjacent_link_ids` — no SQL, no haversine math, no
notebook cell, written by the agent at answer time.

---

## Roadmap: same treatment for the rest of CAIDA's stack

Not built for the hackathon, but each of these is a strong candidate for the identical
function-plus-CLI-plus-output-contract treatment, since `matthew-plus-plus.md`'s stated goal is
every CAIDA tool and dataset, not just Ark:

- **BGP / RouteViews** (`matthewpp.bgp.lookup_prefix_origin`, `matthewpp.bgp.rib_snapshot_at`) — the missing piece `diagnose_path_anomaly`'s `bgp_context` stub is waiting on; needed to fully automate the SDSC-detour-style diagnosis.
- **RPKI / IRR** (`matthewpp.rpki.validate_prefix`) — cross-check a flagged prefix's ROA/route-object status during a diagnosis.
- **DNS at scale / OpenINTEL** (`matthewpp.dns.historical_hosting_for_domain`) — longitudinal version of Category 4, beyond what a live Ark DNS probe can see.
- **IYP graph** (`matthewpp.iyp.cypher_query`) — a constrained, parameterized Cypher-template wrapper (not open-ended query generation) for cross-dataset joins.

---

## Appendix: shared output contract

Every function above returns the same envelope, so the agent (or any downstream code) parses a
response exactly once, ever:

```json
{
  "status": "ok",
  "function": "matthewpp.ark.ping.ping",
  "parameters": { "target": "google.com", "vp_filter": {"region": "africa"}, "duration_s": 10 },
  "data": [ ],
  "warnings": [ ],
  "provenance": {
    "timestamp_utc": "2026-08-19T00:00:00Z",
    "vp_count_used": 12,
    "dataset_or_run_id": "tr_20260819_001"
  }
}
```

On failure, `status` is `"error"`, `data` is `null`, and an `error` object
(`{type, message}`) is added — one of the `Raises:` exception types documented per function,
never a raw stack trace.
