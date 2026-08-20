# ITDK read-only MCP server — implementation plan

**Status:** Approved  
**Created:** 2026-08-19  
**Scope:** A basic Dockerized MCP server written in Python (Flask) that authenticates callers with one
runtime-injected master key and exposes safe, deterministic, read-only access
to the supplied CAIDA ITDK PostgreSQL schema.

## Outcome

An MCP client can authenticate to a network service over a loopback/internal connection, discover a small set of
purpose-built tools, and run bounded lookups and searches against `caida_itdk`.
Instead of returning large datasets directly in the MCP response and overflowing the agent's context, the server saves the query results as a dataframe file (e.g., CSV) to a shared volume and returns JSON metadata (including the file path and row count). The agent can then process this file on its own. The database credentials provided to the server must have only read privileges, as the server does not own the database.

## Assumptions and non-goals

- PostgreSQL connection details and the master key are injected as environment
  variables when the container starts; they are never committed or logged.
- The server does not own the database; it assumes the injected credentials are read-only.
- This version has no user accounts, per-client authorization, rate
  limiting, write operations, background jobs, or data mutation.
- The supplied tables and view are the complete initial data surface.
- We do not artificially limit the returns of the database. Instead of limits, results are written to files to handle large data volume efficiently.
- Tool inputs are intentionally narrower than SQL. New search concepts require
  a reviewed tool or a reviewed extension to an existing tool.

## Proposed architecture

```
MCP client
  |  Authorization: Bearer <master key>
  v
HTTP+SSE MCP server (Python / Starlette ASGI composition)
  |  Loopback / internal network only
  v
deterministic tool handler  --->  read-only PostgreSQL connection pool
  |                                  |
  +-- dataframe file (CSV) <---------+-- parameterized SELECT
  |   (saved to shared volume)
  v
structured JSON metadata returned to client
```

Use the Python MCP SDK's ASGI SSE (Server-Sent Events) transport beneath a
Starlette composition root. Starlette adapts Flask for the operational routes;
strict bearer ASGI middleware runs before every MCP request. Tool handlers call a thin data-access layer; neither the
tool layer nor a client may supply raw SQL, table names, or sort expressions. Use PostgreSQL parameter binding for every caller-controlled
value.

### Configuration contract

Document variable **names** in `.env.example` and `docs/operations/` without
values. Proposed minimum variables:

| Variable | Purpose |
| --- | --- |
| `MCP_MASTER_KEY` | Shared secret used to authenticate requests. |
| `DATABASE_URL` | PostgreSQL connection string for a read-only database role; mutually exclusive with split mode. |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME`, `DB_PASSWORD` | Complete split alternative to `DATABASE_URL`; the application safely assembles the PostgreSQL URL. |
| `MCP_HOST` | Server bind host (default: `127.0.0.1` for loopback/internal). |
| `MCP_PORT` | Server port. |
| `DB_POOL_MAX` | Maximum database connections. |
| `QUERY_TIMEOUT_MS` | PostgreSQL statement timeout for tool queries. |
| `OUTPUT_DIR` | Directory to save dataframe files (mounted as a shared volume). |
| `LOG_LEVEL` | Application log level; request secrets must be redacted. |

## Authentication and database safety

1. Require `Authorization: Bearer <MCP_MASTER_KEY>` on every MCP HTTP request. Reject missing or invalid values
   with a generic `401`; compare secrets in constant time where supported.
2. Bind only to the loopback interface (`127.0.0.1`) or the internal Docker network. We assume no external exposure, so TLS termination is not required for this phase.
3. We do not own the database. The provided URL or split configuration must correspond to a role with `LOGIN`, `CONNECT`, schema
   `USAGE`, and `SELECT` only. 
4. Set connection/session protections: `default_transaction_read_only=on`, a
   bounded `statement_timeout`, `lock_timeout`, an application name, and a
   conservative pool size.
5. Use static SQL templates containing only `SELECT`; parameterize all input. Do not add a
   general query, arbitrary filter, or arbitrary join tool.
6. Return generic internal failures to callers while logging an error code and
   sanitized context.

## Data-access model

The initial relations and allowed join paths are:

| Relation | Primary access patterns | Important constraint |
| --- | --- | --- |
| `itdk_link_endpoints` | link ID lookup; links for node | Join by `node_id`, not `endpoint_token`. |
| `itdk_node_as` | ASNs for node; nodes by ASN | `(node_id, asn)` primary key; `asn` indexed. |
| `itdk_node_geolocation` | location for node; country/bounding-area search | `(country, longitude)` index; avoid unbounded city scans. |
| `itdk_router_hostnames` | exact/prefix hostname lookup; IP lookup | `(ip, hostname)` key; hostname indexed. |
| `v_itdk_ifaces_transit` | interfaces for node/link | Amended 2026-08-19: no such view exists on the supplied database. Interfaces are instead derived from `itdk_link_endpoints.endpoint_token`; see [ADR 0006](../decisions/0006-derive-transit-interfaces-from-endpoint-tokens.md). |

## Deterministic tool contract

### Shared response envelope

To keep the agent's context small while supporting large data returns without artificial limits, tools will save the query results as a CSV file to the configured `OUTPUT_DIR` (which should be a shared volume between the MCP server and the agent). The tool returns structured JSON metadata pointing to the file.

```json
{
  "file_path": "/app/outputs/get_node_profile_N1967_1692464738.csv",
  "row_count": 1,
  "columns": [
    {"name": "node_id", "type": "string"},
    {"name": "asn", "type": "int64"}
  ]
}
```

This ensures agents can deterministically read the file to reconstruct a dataframe predictably, without parsing prose or overwhelming their context window.

### Initial tool set

| Tool | Required input | Optional bounded input | Fixed output / query intent |
| --- | --- | --- | --- |
| `get_node_profile` | `node_id` | `include` enum for `asn`, `geolocation`, `interfaces`, `links` | A single node’s joined profile saved to file. |
| `find_nodes_by_asn` | `asn` | none | `node_id`, `asn`, `method`, ordered by `node_id`; indexed ASN filter. |
| `search_nodes_by_geolocation` | `country` | `longitude_min`, `longitude_max`, `latitude_min`, `latitude_max` | Node geography fields using country plus bounded range predicates and stable ordering. |
| `get_link_endpoints` | `link_id` | none | All endpoint rows for exactly one link, ordered by `endpoint_ordinal`. |
| `find_links_for_node` | `node_id` | none | Link endpoint rows filtered on indexed `node_id`, ordered by `(link_id, endpoint_ordinal)`. |
| `get_transit_interfaces` | exactly one of `node_id` or `link_id` | none | Interface rows from the approved view, ordered on documented keys. |
| `lookup_router_hostnames` | exactly one of `ip`, `hostname_exact`, `hostname_prefix` | none | IP/hostname rows. Prefix matching must use an index-compatible pattern where possible. |

## Implementation phases

### 1. Bootstrap the service

**Implementation status:** Completed 2026-08-19.

1. Initialize a Python project using Flask and the official Python MCP SDK.
2. Create the project manifest (e.g. `requirements.txt` or `pyproject.toml`), source layout, `.gitignore`, and `.env.example`.
3. Add `Dockerfile` and `docker-compose.yml`. Compose passes environment variables at runtime and maps a shared volume for `OUTPUT_DIR`.
4. Add configuration parsing, startup validation, structured logging with redaction, and health endpoints.

### 2. Establish the read-only data boundary

**Implementation status:** Completed 2026-08-19.

1. Implement a database pool (using `psycopg` or `asyncpg`) that applies read-only and timeout settings.
2. Implement one repository/data-access module per approved query family with fixed projections and parameterized predicates.
3. Write functions to stream query results directly to CSV files in the `OUTPUT_DIR`.

### 3. Implement the MCP boundary

**Implementation status:** Completed 2026-08-19.

1. Set up the Flask app to handle MCP SSE connections.
2. Add constant-time bearer-key middleware around the Flask routes.
3. Register the deterministic tools with strict JSON Schemas.
4. Add the shared result serializer that produces the JSON metadata pointing to the generated CSV files.

### 4. Verify functionality and safety

**Implementation status:** Test suites completed 2026-08-19; dynamic execution
pending a user or GitHub Actions run per `AGENT.md`.

1. Unit-test configuration parsing, authentication, type serialization, and errors.
2. Integration-test against an ephemeral PostgreSQL fixture.
3. Verify that the files generated in `OUTPUT_DIR` are correctly formatted and accessible.

### 5. Package and operate

**Implementation status:** Packaging, operations documentation, and CI
completed 2026-08-19; dynamic CI/test/image execution remains pending a user or
GitHub Actions run per `AGENT.md`.

1. Build a minimal non-root production image.
2. Document local startup, shared volume configurations, and troubleshooting.
3. Add CI for formatting, unit tests, and image build.

## Acceptance criteria

- Container startup fails safely without a valid master key or database config.
- Every MCP request requires the configured bearer master key.
- Tools stream results to a file and return concise metadata. Limits are not artificially applied to query results.
- The database is assumed read-only; attempts to write, create, or alter objects fail.
- Returned JSON metadata allows agents to confidently locate and parse the dataframe files.
