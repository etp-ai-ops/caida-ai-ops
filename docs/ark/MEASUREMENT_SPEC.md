# Measurement Subsystem Specification

Covers the Ark capability layer now packaged as `caida_ai_ops.ark`, its 25 tools within the
shared 43-tool `caida_ai_ops.server`, the CLI, and the demo notebook.

Status: demo mode is fully tested. The live backend uses scamper's instance/response queue;
running it additionally requires an authorized Ark host, Python scamper bindings, mux socket,
and matching group access.

## 1. Architecture

```
matthewpp_core.py           <- shared: output envelope, formatting. No knowledge of Ark.
        ^
        |
ark_measurement.py          <- the capability layer: 25 tools, hard limits,
        ^                       demo/live backend switch, local run store.
        |
        +---------------------+----------------------+
        |                     |                      |
mcp_server.py                ark_measurement.py's  notebooks/
(25 of its 43 MCP tools,     own CLI (argparse,   ark_measurement_demo.ipynb
 1 per function; the other   python3 ...py --help) (walkthrough, pre-run)
 18 wrap AS Rank and ITDK)
```

`itdk_db.py` sits alongside `ark_measurement.py` as an independent second module on the
same `matthewpp_core` — neither measurement module depends on the other.

Every entry point (MCP tool, CLI subcommand, or a direct Python import) calls the exact same
`ark_measurement.py` functions and gets the exact same output shape back. There is no
capability, hard limit, or behavior that exists in one entry point and not another.

## 2. Design principle

An agent (or a human) is never asked to write new measurement code. Every capability is a
predefined, fully-typed, fully-docstringed Python function; the caller's only job is to fill
in parameters. See [`matthew-plus-plus.md`](matthew-plus-plus.md) for the motivating
rationale and [`ark-capabilities-and-modules.md`](ark-capabilities-and-modules.md) for the
original capability-catalog design this module implements.

## 3. Output contract

Every public function in `ark_measurement.py` — regardless of entry point — returns this
exact envelope (built by `matthewpp_core.envelope`/`Result`):

```json
{
  "status": "ok",
  "function": "matthewpp.ark.ping.ping",
  "parameters": { "...": "the fully-bound arguments, including defaults" },
  "data": { "...": "function-specific payload, see §5" },
  "warnings": ["..."],
  "provenance": { "timestamp_utc": "2026-08-19T18:33:00Z", "...": "function-specific extras" }
}
```

On failure, the shape is identical except:

```json
{
  "status": "error",
  "data": null,
  "error": { "type": "MeasurementLimitExceededError", "message": "..." }
}
```

`error.type` is always one of the exception class names in §4 (or `"InternalError"` for a
truly unexpected exception) — never a raw Python traceback. A caller only ever needs to
branch on `status`; nothing else in the response shape changes based on success/failure.

**Guarantee**: this envelope is produced by a decorator (`@envelope(...)`) wrapping every
function once, at definition time — individual functions cannot bypass it, and cannot return
a differently-shaped success value (they return a `Result(data=..., warnings=..., provenance_extra=...)`
or a bare value; the decorator does the rest).

## 4. Exception taxonomy (`ark_measurement.py`)

| Exception | Raised when |
|---|---|
| `ArkMuxUnavailableError` | The Ark mux socket / `scamper` package is unreachable (live mode only) |
| `NoMatchingVPsError` | A vantage-point filter matched zero active VPs |
| `InvalidFilterError` | A filter argument (e.g. `near_lat_lon`) was malformed |
| `UnsupportedMethodError` | An unsupported ping/traceroute `method` was requested |
| `UnsupportedQtypeError` | An unsupported DNS record `qtype` was requested |
| `UnknownRunIdError` | A referenced traceroute `run_id`/baseline label doesn't exist |
| `MeasurementLimitExceededError` | Any hard limit in §6 was exceeded |

All inherit from `matthewpp_core.MatthewPPError`, the single base class the `@envelope`
decorator catches and converts to `status: "error"`.

## 5. Capabilities

Nineteen functions across five categories. Every one accepts a `vp_filter: dict | None` with
keys `region`, `country`, `asn`, `tag`, `vp_ids`, `near_lat_lon`, `radius_km`, `ipv6_only`,
`active_only` (all optional; `None`/unfiltered = every active VP, which triggers a warning
since it's expensive) unless noted otherwise.

### 5.1 Vantage-point discovery

| Function | Purpose | Key params |
|---|---|---|
| `list_vps` | List VPs matching filters | full `vp_filter` set, `limit` |
| `get_vp_by_id` | Fetch one VP by ID | `vp_id` |
| `count_vps` | Count matches without fetching records | subset of `vp_filter` |
| `nearest_vps` | N nearest VPs to a lat/lon point | `lat`, `lon`, `n` |

Not subject to the active-probing hard limits in §6 (these are metadata lookups, not probes).

### 5.2 Ping

| Function | Purpose | Key params |
|---|---|---|
| `ping` | Ping a target from matched VPs | `target`, `duration_s`/`count`, `interval_ms`, `timeout_ms`, `method`, `parallel` |
| `is_reachable` | Single-probe reachability check | `target`, `timeout_ms` (wraps `ping(count=1)`) |
| `ping_multi_targets` | Ping several targets from the same VP set | `targets` (≤20), `**ping_kwargs` |
| `compare_ping_regions` | Compare avg latency between two regions | `target`, `region_a`, `region_b` |

`method` ∈ `{icmp-echo, icmp-time, tcp-syn, udp}`.

### 5.3 Traceroute & route-anomaly detection

| Function | Purpose | Key params |
|---|---|---|
| `traceroute` | Traceroute from matched VPs, persists a `run_id` | `target`, `max_hops`, `attempts_per_hop`, `wait_ms`, `method` |
| `get_run` | Fetch a stored run by `run_id` or baseline label | `run_id` |
| `list_runs` | List recent stored runs | `target`, `vp_id`, `limit` |
| `save_run_as_baseline` | Label a run for later reference | `run_id`, `label` |
| `compare_paths` | Fresh traceroute, diffed vs. a baseline and/or flagged for suspect transit | `target`, `baseline_run_id`, `flag_transit_asn`, `flag_transit_ip_prefix` |
| `check_transit` | One-shot version of `compare_paths` (no baseline) | `target`, `asn`, `ip_prefix` |
| `list_hops_by_asn` | Hops belonging to an AS within a stored run | `run_id`, `asn` |

`method` ∈ `{icmp-paris, udp-paris, tcp}`. Every `traceroute` call is persisted — see §7.

### 5.4 DNS

| Function | Purpose | Key params |
|---|---|---|
| `dns_query` | DNS query from matched VPs | `qname`, `qtype`, `resolver`, `timeout_ms` |
| `dns_divergence_report` | Groups `dns_query` answers by distinct answer set | `qname`, `qtype` |
| `check_dnssec_valid` | Simplified per-VP DS+DNSKEY presence check | `qname` |

`qtype` ∈ `{A, AAAA, NS, MX, TXT, DS, DNSKEY, CAA, SOA}`. `check_dnssec_valid` is a presence
heuristic, not full chain-of-trust cryptographic validation.

### 5.5 Multi-vantage-point root-cause diagnosis

| Function | Purpose | Key params |
|---|---|---|
| `diagnose_path_anomaly` | Orchestrates VP sampling + `compare_paths`, returns evidence not a verdict | `target`, `suspect_asn`, `suspect_ip_prefix`, `sample_size` |
| `bgp_context_for_asn` | Roadmap stub for BGP cross-reference | `asn` — always returns `{"status": "unavailable", ...}` today |

`diagnose_path_anomaly` deliberately does not draw a conclusion — per the
"attribution without overclaiming" principle, it returns `pct_vps_affected`,
`affected_regions`, `affected_vp_ids`, and (currently unavailable) `bgp_context` for the
caller to reason over.

## 6. Hard limits

Enforced unconditionally inside the function (not just at the CLI/MCP layer), as a hard
rejection (`MeasurementLimitExceededError`) — never a silent clamp:

| Limit | Constant | Value |
|---|---|---|
| Ping duration | `MAX_PING_DURATION_S` | 60.0s |
| Ping count | `MAX_PING_COUNT` | 120 |
| Ping interval | `MIN_PING_INTERVAL_MS` | ≥200ms |
| Ping/DNS timeout | `MIN_PING_TIMEOUT_MS` / `MAX_PING_TIMEOUT_MS` / `MIN_DNS_TIMEOUT_MS` / `MAX_DNS_TIMEOUT_MS` | 100–10000ms |
| Ping total probes (VPs × count) | `MAX_TOTAL_PING_PROBES` | 2000 |
| Traceroute max hops | `MAX_TRACEROUTE_HOPS` | 64 |
| Traceroute attempts/hop | `MAX_TRACEROUTE_ATTEMPTS_PER_HOP` | 5 |
| Traceroute wait | `MIN_TRACEROUTE_WAIT_MS` / `MAX_TRACEROUTE_WAIT_MS` | 100–5000ms |
| VPs per single call (ping/traceroute/DNS) | `MAX_VPS_PER_MEASUREMENT` | 50 |
| `ping_multi_targets` target count | `MAX_MULTI_TARGETS` | 20 |
| `diagnose_path_anomaly` sample size | `MAX_DIAGNOSE_SAMPLE_SIZE` | 50 |

Rationale: a single call — from a human via the CLI, or an agent via MCP — must not be able
to launch an overload against Ark's shared vantage-point pool or against whatever it's
probing. There is no override parameter; the only way past a limit is to narrow the request
(fewer VPs, shorter duration, split across multiple calls).

## 7. State: the local run store

`traceroute()` persists every run to a local JSON file (`MATTHEWPP_RUN_STORE`, default
`~/.matthewpp/runs.json`) under a generated `run_id` (`tr_<UTC timestamp>_<6 hex chars>`).
Structure:

```json
{ "runs": { "<run_id>": {"run_id", "target", "timestamp_utc", "traces"} },
  "baselines": { "<label>": "<run_id>" } }
```

`get_run`/`list_runs`/`save_run_as_baseline`/`compare_paths`/`list_hops_by_asn` all read or
write this store. It is local to the machine running `ark_measurement.py` (not shared across
MCP server instances unless they share a filesystem), and is outside the git repo (never
committed).

## 8. Backends: demo vs. live

Selected per-call via `matthewpp_core.is_demo_mode()`, which reads `MATTHEWPP_DEMO` from the
environment (`"1"`/`"true"`/`"yes"` = demo). No code path needs to know which backend is
active beyond that check.

- **Demo**: `_DEMO_VPS` — 10 fixed vantage points across Africa, Europe, East Asia, South
  America, North America, and Oceania (one, `ark-sdc-us`, tagged `caida` at UC San Diego,
  AS 7377 — used by the demo traceroute/diagnose fixtures to simulate the SDSC-detour pattern
  `matthew-plus-plus.md` is built around). Ping RTTs are a deterministic hash of
  `(vp_id, target)`, not random, so demo runs are reproducible. Every demo response carries an
  explicit warning string.
- **Live**: `_get_scamper_ctrl()` lazily imports `scamper.ScamperCtrl` and connects to
  `MATTHEWPP_MUX` (default `/run/ark/mux`). Measurements add VPs, queue work per instance,
  close the queue, and drain responses while restoring requested VP order.

## 9. Known gaps

1. **`bgp_context_for_asn` is a stub.** Always returns `{"status": "unavailable", ...}`.
   `diagnose_path_anomaly` calls it but degrades gracefully — a real RouteViews-backed BGP
   module (see Roadmap in `ark-capabilities-and-modules.md`) would slot in at this one call
   site without changing anything else.
2. **DNSSEC check is a heuristic**, not full chain-of-trust validation (§5.4).

## 10. Entry points

### 10.1 CLI (`ark_measurement.py`)

One subcommand per function (`vps`, `ping`, `traceroute`, `compare-paths`, `dns`, `diagnose`,
etc., plus result and IP-metadata commands), `--format {json,csv,md}` and `--output-file` on every
command. `python3 ark_measurement.py --help` / `<subcommand> --help` for the full flag list.
Global flags (`--format`, `--output-file`) must precede the subcommand (an argparse
constraint, not a bug).

### 10.2 MCP server (`caida_ai_ops.server`)

Built on `mcp.server.mcpserver.MCPServer` (the `mcp` package's current high-level API — note
this replaced the older `FastMCP` name in `mcp` v2.0.0). The unified server has 43 tools:
25 Ark, seven AS Rank, four assignment analyses, and seven generic ITDK repository tools.

| MCP tool | Wraps |
|---|---|
| `list_ark_vps` | `list_vps` |
| `get_ark_vp` | `get_vp_by_id` |
| `count_ark_vps` | `count_vps` |
| `nearest_ark_vps` | `nearest_vps` |
| `ping_ark` | `ping` |
| `is_ark_reachable` | `is_reachable` |
| `ping_ark_multi_targets` | `ping_multi_targets` |
| `compare_ark_ping_regions` | `compare_ping_regions` |
| `traceroute_ark` | `traceroute` |
| `get_ark_traceroute_run` | `get_run` |
| `list_ark_traceroute_runs` | `list_runs` |
| `save_ark_traceroute_baseline` | `save_run_as_baseline` |
| `compare_ark_paths` | `compare_paths` |
| `check_ark_transit` | `check_transit` |
| `list_ark_hops_by_asn` | `list_hops_by_asn` |
| `dns_query_ark` | `dns_query` |
| `dns_divergence_report_ark` | `dns_divergence_report` |
| `check_ark_dnssec_valid` | `check_dnssec_valid` |
| `diagnose_ark_path_anomaly` | `diagnose_path_anomaly` |
| `list_ark_results` | `list_results` |
| `get_ark_result` | `get_result` |
| `export_ark_result` | `export_result` |
| `find_ips_in_city` | `find_ips_in_city` |
| `lookup_ip_metadata` | `lookup_ip` |
| `enrich_ark_result` | `enrich_result` |

**Parameter flattening rule**: every tool that takes a `vp_filter` flattens it into individual
named parameters (`region`, `country`, `asn`, `tag`, `vp_ids`, `active_only`) — a nested JSON
object is harder for an LLM to construct correctly than flat named args. `list_ark_vps`/
`nearest_ark_vps` additionally expose `near_lat`/`near_lon`/`radius_km`/`ipv6_only` since geo
search is their whole purpose; other tools omit these — an agent needing radius-based VP
selection for e.g. `ping_ark` calls `list_ark_vps`/`nearest_ark_vps` first and passes the
resulting `vp_ids` in.

Every Ark tool call returns the identical §3 envelope. Schemas are generated from the
running MCP v2 server and verified by the unified catalog test rather than checked in as a
stale generated manifest.

Transport: stdio (`mcp.run()` default) — see `README.md`'s "MCP server" section for client
setup (Claude Desktop / Claude Code).

### 10.3 Notebook (`notebooks/ark_measurement_demo.ipynb`)

Walks through discovery → ping → traceroute/transit-flagging → multi-VP diagnosis → DNS
divergence → a hard-limit rejection, in demo mode, shipped pre-run with real captured output.

## 11. File manifest

| File | Role |
|---|---|
| `src/caida_ai_ops/core.py` | Output envelope, persistence, formatting, demo-mode flag |
| `src/caida_ai_ops/ark.py` | Ark capabilities and CLI |
| `src/caida_ai_ops/server.py` | Shared MCP v2 catalog, 43 tools |
| `examples/notebooks/ark_measurement_demo.ipynb` | Pre-run walkthrough |
| `README.md` | Setup + quickstart (this doc is the deeper reference) |
