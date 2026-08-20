# Unified service architecture

`caida-ai-ops` is a Python 3.12 package with one MCP v2 `MCPServer` catalog. The catalog
contains 43 tools: 25 Ark tools, seven AS Rank tools, four assignment-oriented ITDK
analyses, and seven generic ITDK repository tools. The same catalog runs over stdio via
`caida-ai-ops` and over authenticated Streamable HTTP via `caida-itdk-service`.

The generic ITDK layer retains the original safety boundary: a bounded psycopg3 pool starts
sessions read-only, applies statement and lock timeouts, and exposes only package-owned,
parameterized `SELECT` statements. Results stream through named cursors to atomic UTF-8 CSV
files instead of being buffered in memory. The assignment analyses retain their JSON output
envelope and demo backend while they are incrementally moved onto the shared repository.

HTTP deployment validates configuration once, creates the output directory, owns the pool
through the ASGI lifespan, and protects `/mcp` with constant-time bearer authentication.
`/healthz` and `/readyz` are intentionally unauthenticated and return no configuration data.
The stdio server initializes generic ITDK database resources lazily, so Ark and AS Rank tool
discovery and demo calls do not require database credentials.

Ark probing enforces hard fan-out and probe-count limits. Its production backend uses the
scamper instance/response-queue model and `MATTHEWPP_MUX` (default `/run/ark/mux`). Packet-
emitting results are archived beneath `MATTHEWPP_RESULTS_DIR`, or `OUTPUT_DIR/ark-results`
in a service deployment. Live Ark also requires the host's scamper Python package, mux
socket mount, and matching group permissions; the standard image is suitable for demo and
database workloads unless those host integrations are supplied.

Traceroute archives include the measuring VP's coordinates. During enrichment, hop location
claims are checked against the fastest possible round trip through fibre. Physically
impossible claims remain visible but are demoted to `confidence: contradicted-by-rtt` with an
`rtt_check` evidence object. A positive consistency result means only “not excluded.” Hosts
without `/data/external/geofeed-whois` can build a compact public-mirror index with
`python -m caida_ai_ops.geodata build-geofeed-cache`.
