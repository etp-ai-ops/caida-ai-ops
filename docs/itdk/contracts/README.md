# MCP and ITDK contracts

The unified server exposes 43 tools over stdio and MCP Streamable HTTP. The HTTP endpoint is
`POST /mcp` (with the other methods required by the MCP transport) and requires exactly one
`Authorization: Bearer <MCP_MASTER_KEY>` header. Tool discovery itself does not open a
database connection.

The seven generic ITDK inputs retain strict Draft 2020-12 validation with no arbitrary SQL,
relation, projection, join, ordering, or filter inputs. They are:

- `get_node_profile`
- `find_nodes_by_asn`
- `search_nodes_by_geolocation`
- `get_link_endpoints`
- `find_links_for_node`
- `get_transit_interfaces`
- `lookup_router_hostnames`

Successful generic ITDK calls return `{file_path, row_count, columns}`. CSV files use UTF-8,
`\n` endings, `\N` for null, deterministic column ordering, mode `0640`, and an atomic
temporary-file rename. Invalid inputs return `INVALID_ARGUMENT`; execution failures return
`INTERNAL_ERROR`. Logs never include caller values, SQL, output paths, or credentials.

Ark, AS Rank, and assignment-oriented ITDK tools return the common JSON envelope
`{status, function, parameters, data, warnings, provenance}`. Demo responses carry an
explicit synthetic-data warning. Ark packet-emitting tools are bounded and are not described
as read-only or idempotent. Generic ITDK and AS Rank query tools are read-only.

The returned ITDK `file_path` is a server filesystem path. Container consumers mount the
`caida-ai-ops-outputs` volume at `/app/outputs`, or perform an explicit trusted prefix mapping.
