# ITDK read-only MCP server — implementation plan

**Status:** Proposed  
**Created:** 2026-08-19  
**Scope:** A basic Dockerized MCP server that authenticates callers with one
runtime-injected master key and exposes safe, deterministic, read-only access
to the supplied CAIDA ITDK PostgreSQL schema.

## Outcome

An MCP client can authenticate to a network service, discover a small set of
purpose-built tools, run bounded lookups and searches against `caida_itdk`,
and receive a tabular result in MCP structured content. The client/agent can
then turn that data into its preferred dataframe format locally. The server
never accepts arbitrary SQL and the database credentials have only read
privileges.

## Assumptions and non-goals

- PostgreSQL connection details and the master key are injected as environment
  variables when the container starts; they are never committed or logged.
- This first version has no user accounts, per-client authorization, rate
  limiting, write operations, background jobs, or data mutation.
- The supplied tables and view are the complete initial data surface.
- “Dataframe” means an explicitly typed, JSON-serializable table envelope in
  `structuredContent`, not a language-specific in-memory object. MCP transports
  JSON; agents can deterministically reconstruct a dataframe from `columns` and
  `rows` (or records) without scraping prose.
- Tool inputs are intentionally narrower than SQL. New search concepts require
  a reviewed tool or a reviewed extension to an existing tool.

## Proposed architecture

```
MCP client
  |  Authorization: Bearer <master key>
  v
Streamable HTTP MCP server
  |  authenticate once per request; validate typed tool input
  v
deterministic tool handler  --->  read-only PostgreSQL connection pool
  |                                  |
  +-- structured table envelope <----+-- parameterized, bounded SELECT only
```

Use the MCP SDK’s streamable HTTP transport (with a separately configurable
bind address and port). Keep authentication in HTTP middleware before MCP
request handling. Tool handlers call a thin data-access layer; neither the
tool layer nor a client may supply raw SQL, table names, sort expressions, or
unbounded limits. Use PostgreSQL parameter binding for every caller-controlled
value.

### Configuration contract

Document variable **names** in `.env.example` and `docs/operations/` without
values. Proposed minimum variables:

| Variable | Purpose |
| --- | --- |
| `MCP_MASTER_KEY` | Shared secret used to authenticate requests. |
| `DATABASE_URL` | PostgreSQL connection string for a read-only database role. |
| `MCP_HOST` | Server bind host; default should be documented explicitly. |
| `MCP_PORT` | Server port. |
| `DB_POOL_MAX` | Maximum database connections. |
| `QUERY_TIMEOUT_MS` | PostgreSQL statement timeout for tool queries. |
| `MAX_PAGE_SIZE` | Hard upper bound for returned rows. |
| `LOG_LEVEL` | Application log level; request secrets must be redacted. |

At process startup, fail closed if required settings are missing, malformed, or
the master key is unsafe/empty. Parse settings once with a typed configuration
module and never expose them from an MCP tool, error, health response, or log.

## Authentication and database safety

1. Require `Authorization: Bearer <MCP_MASTER_KEY>` on every MCP HTTP request
   (including initialization and tool calls). Reject missing or invalid values
   with a generic `401`; compare secrets in constant time where supported.
2. Bind only to the explicitly configured interface. For deployments outside a
   trusted local network, place TLS termination in a reverse proxy or configure
   TLS at the service edge; a bearer key over plaintext HTTP is not safe on an
   untrusted network.
3. Create and use a dedicated PostgreSQL role with `LOGIN`, `CONNECT`, schema
   `USAGE`, and `SELECT` only on the five approved relations. It must have no
   write, DDL, ownership, role-management, or superuser privileges.
4. Set connection/session protections: `default_transaction_read_only=on`, a
   bounded `statement_timeout`, `lock_timeout`, an application name, and a
   conservative pool size. Enforce timeouts server-side even when API-level
   validation exists.
5. Use static SQL templates containing only `SELECT`; parameterize all input.
   Set a server-controlled `LIMIT` in every list/search query. Do not add a
   general query, arbitrary filter, or arbitrary join tool.
6. Return generic internal failures to callers while logging an error code and
   sanitized context. Do not log authorization headers, keys, `DATABASE_URL`,
   or result payloads by default.

## Data-access model

The initial relations and allowed join paths are:

| Relation | Primary access patterns | Important constraint |
| --- | --- | --- |
| `itdk_link_endpoints` | link ID lookup; links for node | Join by `node_id`, not `endpoint_token`. |
| `itdk_node_as` | ASNs for node; nodes by ASN | `(node_id, asn)` primary key; `asn` indexed. |
| `itdk_node_geolocation` | location for node; country/bounding-area search | `(country, longitude)` index; avoid unbounded city scans. |
| `itdk_router_hostnames` | exact/prefix hostname lookup; IP lookup | `(ip, hostname)` key; hostname indexed. |
| `v_itdk_ifaces_transit` | interfaces for node/link | View has multiple rows per node/link; preserve this fact. |

Design queries around supplied indexes. For large result domains, require at
least one selective filter, apply a small default limit, cap the requested
limit, and use a stable cursor strategy (prefer keyset pagination on a declared
unique ordering over offset pagination). Do not claim exact global counts
unless a dedicated bounded/count tool is later approved and performance-tested.

## Deterministic tool contract

All tools must have JSON Schema input validation, documented defaults and
maximums, explicit column names/types, and a `next_cursor` only for list-like
results. Tool names below are the implementation target, subject to final SDK
naming conventions.

### Shared response envelope

Successful data tools should return structured content shaped like:

```json
{
  "columns": [
    {"name": "node_id", "type": "string"},
    {"name": "asn", "type": "int64"}
  ],
  "rows": [["N1967", "64500"]],
  "row_count": 1,
  "next_cursor": null,
  "truncated": false
}
```

Serialize PostgreSQL `bigint` values as strings to avoid JSON integer precision
loss in JavaScript clients. Serialize `inet` as strings, nullable values as
`null`, and numeric coordinates as JSON numbers. The textual content may give a
one-line human summary, but agents must be able to rely solely on
`structuredContent`. Return a typed `INVALID_ARGUMENT`, `NOT_FOUND`, or
`INTERNAL_ERROR`-style MCP tool error with no secret or SQL text.

### Initial tool set

| Tool | Required input | Optional bounded input | Fixed output / query intent |
| --- | --- | --- | --- |
| `get_node_profile` | `node_id` | `include` enum for `asn`, `geolocation`, `interfaces`, `links` | A single node’s joined profile; child collections separately limited and marked truncated. |
| `find_nodes_by_asn` | `asn` | `limit`, `cursor` | `node_id`, `asn`, `method`, ordered by `node_id`; indexed ASN filter. |
| `search_nodes_by_geolocation` | `country` | `longitude_min`, `longitude_max`, `latitude_min`, `latitude_max`, `limit`, `cursor` | Node geography fields using country plus bounded range predicates and stable ordering. |
| `get_link_endpoints` | `link_id` | none | All endpoint rows for exactly one link, ordered by `endpoint_ordinal`. |
| `find_links_for_node` | `node_id` | `limit`, `cursor` | Link endpoint rows filtered on indexed `node_id`, ordered by `(link_id, endpoint_ordinal)`. |
| `get_transit_interfaces` | exactly one of `node_id` or `link_id` | `limit`, `cursor` | Interface rows from the approved view, ordered on documented keys. |
| `lookup_router_hostnames` | exactly one of `ip`, `hostname_exact`, `hostname_prefix` | `limit`, `cursor` | IP/hostname rows. Prefix matching must use an index-compatible pattern where possible and impose a narrow prefix minimum length. |

Validation rules to implement consistently:

- Node IDs and link IDs are non-empty, length-bounded opaque strings; reject
  control characters.
- ASN is a positive integer within PostgreSQL `bigint` range and is parameter
  bound, never interpolated.
- Country is an uppercase two-letter ISO code. Coordinates are finite and
  bounded to valid latitude/longitude ranges; minimums cannot exceed maximums.
- `limit` defaults to a conservative value (for example 100) and cannot exceed
  the configured hard maximum (for example 1,000). A request that exceeds the
  maximum is rejected rather than silently enlarged.
- Cursors are opaque, base64url-encoded, versioned server state containing only
  the validated filter fingerprint and final sort key. Reject cursors that do
  not match the current filters/tool/version.
- Hostname prefix search requires a minimum prefix length (for example three)
  and uses escaped literal matching, preventing wildcard semantics from being
  supplied by the client.

## Implementation phases

### 1. Bootstrap the service

1. Select the implementation language and current official MCP SDK; record the
   choice in a decision record. Prefer an SDK with typed tool schemas,
   Streamable HTTP support, and reliable PostgreSQL pooling.
2. Create the project manifest, source layout, lint/format/test commands,
   pinned dependency lockfile, `.gitignore`, and `.env.example` containing
   names/placeholders only. Never create or inspect a real `.env`.
3. Add `Dockerfile` and `docker-compose.yml`. Compose passes environment
   variables through at runtime; it must not bake a key or database credentials
   into an image, source layer, build argument, or repository.
4. Add configuration parsing, startup validation, structured logging with
   redaction, `/healthz` (process health only), and `/readyz` (database pool
   connectivity) endpoints. Decide whether health endpoints are network
   restricted or authenticated and document the choice.

### 2. Establish the read-only data boundary

1. Provision the dedicated database role and grants through a separately
   reviewed administrator-run SQL artifact or deployment procedure. Do not make
   the application role a schema owner.
2. Implement a pool factory that applies read-only and timeout settings on each
   connection/session.
3. Implement one repository/data-access module per approved query family with
   fixed projections, parameterized predicates, keyset pagination, and a
   hard-coded/server-clamped `LIMIT`.
4. Create unit tests that assert each static query is a `SELECT`, validates
   parameters, preserves exact ordering, and cannot alter projection, relation,
   predicate structure, or sort clause through tool input.

### 3. Implement the MCP boundary

1. Add constant-time bearer-key middleware around the Streamable HTTP endpoint.
   Test absent, malformed, wrong, and correct headers before any tool executes.
2. Register the seven deterministic tools with strict JSON Schemas and MCP
   descriptions that state scope, cost/limit behavior, and response columns.
3. Add a shared result serializer that produces the documented table envelope
   in `structuredContent`, handles PostgreSQL data types safely, and reports
   truncation/cursors consistently.
4. Add error mapping and request correlation IDs. Ensure emitted errors and
   logs cannot include credentials, header values, raw SQL, or driver internals.

### 4. Verify functionality, safety, and performance

1. Unit-test configuration parsing, authentication, every schema validation
   boundary, cursor decoding/mismatch cases, type serialization, and errors.
2. Integration-test against an ephemeral PostgreSQL fixture containing
   representative duplicate endpoints, nullable hostname/region/city values,
   multiple ASNs, multi-interface nodes, and pagination boundaries. Use a role
   restricted to `SELECT` to prove writes fail.
3. Add MCP protocol tests: initialize, list tools, authenticated invocation,
   rejection without key, and structured-content shape validation.
4. Run `EXPLAIN (ANALYZE, BUFFERS)` against production-like data using the
   exact static query templates and worst permitted inputs. Check index usage,
   timeouts, response sizes, and view behavior; tune projections/indexes only
   with DBA approval.
5. Exercise negative cases: SQL/meta-character payloads, hostile cursors,
   large limits, invalid coordinates, bearer-key leakage checks, canceled
   client requests, unavailable database, slow query, and pool exhaustion.

### 5. Package and operate

1. Build a minimal non-root production image and verify the container only
   receives required runtime settings. Add a Docker health check that does not
   disclose credentials.
2. Document local startup, required environment variable names, reverse-proxy
   TLS expectations, master-key rotation procedure (replace runtime secret and
   restart), database role provisioning, observability, and troubleshooting.
3. Add CI for formatting, static analysis, unit tests, integration tests, image
   build, dependency/security scanning, and secret scanning.
4. Update the contracts, data, operations, and decision docs as implementation
   choices become final. Mark this plan implemented only after the acceptance
   criteria below are met.

## Acceptance criteria

- Container startup fails safely without a valid master key or database config,
  and no secret is emitted.
- Every MCP request requires the configured bearer master key.
- The only callable data operations are the documented deterministic tools;
  no raw SQL or caller-selected table/column/order is accepted.
- The database role and application session are read-only; attempts to write,
  create, or alter objects fail.
- All list/search tools validate inputs, apply a timeout and hard row limit,
  use deterministic ordering, and provide safe pagination/truncation behavior.
- Returned `structuredContent` preserves columns, types, nulls, `bigint`, and
  `inet` values so an agent can reconstruct a dataframe predictably.
- Automated tests cover authorization, access scoping, serialization, error
  redaction, pagination, and read-only enforcement; production-like explain
  results meet the agreed latency/resource budget.
- Docker and operational documentation explain configuration by name without
  containing any secret values.

## Decisions to confirm before coding

1. **Language/SDK:** Choose the MCP SDK/runtime after checking its current
   Streamable HTTP, structured-content, and schema support.
2. **Transport exposure:** Confirm whether the server is loopback/internal only
   or sits behind a TLS-terminating proxy. This determines the concrete Docker
   networking and health endpoint policy.
3. **Database ownership:** Identify the administrator responsible for creating
   the least-privilege PostgreSQL role and approving any performance indexes.
4. **Response budget:** Set target default/max page sizes, maximum serialized
   bytes, statement timeout, and latency SLO using representative query plans.
5. **Node profile shape:** Confirm whether `get_node_profile` should return
   nested collections or be replaced with simple composable table tools only.
6. **Dataframe convention:** Confirm the shared `columns`/positional `rows`
   envelope (compact and type-preserving) or an alternate record-oriented
   convention before clients depend on it.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Broad scans over multi-million-row relations | Require selective filters, index-aligned queries, limits, timeouts, and plan testing. |
| Read-only intent defeated by misconfiguration | Enforce least-privilege grants and read-only session settings in addition to static `SELECT` code. |
| Bearer key exposed in transit or logs | TLS at the edge, constant-time comparison, header redaction, generic errors, and runtime-only injection. |
| Agents receive ambiguous tables | Versioned schemas, explicit columns/types, deterministic order, and structured content. |
| JSON corrupts PostgreSQL types | Define bigint/inet/null/float serialization and cover it with contract tests. |
| View cost or row multiplication surprises callers | Keep a dedicated transit tool, stable keyset pagination, clear tool descriptions, and measure with production-like plans. |
