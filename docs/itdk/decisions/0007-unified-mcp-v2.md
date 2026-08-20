# 0007 — Unified MCP v2 catalog and transports

Status: accepted, superseding the transport and composition choices in 0001 and 0003.

The repository merge requires Ark, AS Rank, and both ITDK surfaces to be installable and
discoverable together. The previous repositories selected incompatible MCP major versions
and separate stdio and legacy-SSE servers.

Use MCP Python SDK v2 and one high-level `MCPServer` catalog. Expose the catalog over stdio
for local agent processes and authenticated Streamable HTTP for service deployments. Keep
health/readiness routes directly in Starlette; Flask and the v1 low-level/SSE adapter are no
longer runtime dependencies.

The generic ITDK validators and dispatcher remain independent from transport. This preserves
their strict schemas, read-only database boundary, safe errors, streaming CSV behavior, and
unit-testability while allowing registration beside the other tool families.
