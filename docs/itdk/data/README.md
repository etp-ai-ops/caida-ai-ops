# Data

The server exposes a strictly read-only, scoped view of CAIDA ITDK data. All
relations are schema-qualified beneath `caida_itdk`; callers cannot select SQL
structure.

| Relation | Fixed projection | Stable ordering |
| --- | --- | --- |
| `itdk_node_as` | `node_id, asn, method` | Profile: `(node_id, asn)`; ASN lookup: `node_id`. |
| `itdk_node_geolocation` | `node_id, continent, country, region, city, latitude, longitude, method` | Profile: `node_id`; search: `(country, longitude NULLS FIRST, latitude NULLS FIRST, node_id)`. |
| `itdk_link_endpoints` | `link_id, endpoint_ordinal, endpoint_token, node_id` | Exact link: `endpoint_ordinal`; node: `(link_id, endpoint_ordinal)`. Joins use `node_id`, not `endpoint_token`. |
| `itdk_router_hostnames` | `ip, hostname` | IP: `(ip, hostname NULLS FIRST)`; exact/prefix hostname: `(hostname, ip)`. |

There is no `v_itdk_ifaces_transit` view on the supplied database; only the four
tables above exist. `get_transit_interfaces` and `get_node_profile`'s
`interfaces` family instead derive `ip, node_id, link_id, flags` from
`itdk_link_endpoints.endpoint_token`, filtered to rows matching
`<node_id>:<ip>` (CAIDA's encoding for an endpoint whose transit interface was
resolved by traceroute; a bare `node_id` token means none was). `flags` has no
source in the supplied schema and is always `NULL`; the column is kept only
for output-contract stability. See
[0006](../decisions/0006-derive-transit-interfaces-from-endpoint-tokens.md).

Geolocation search always requires indexed `country`; coordinate bounds are
optional bound parameters. Hostname prefix treats `%`, `_`, and backslash
literally before appending the server-owned `%` suffix.

The MCP boundary validates identifier, ASN, country, coordinate, IP, hostname,
and prefix bounds before a repository is called. Selector tools accept exactly
one selector, coordinate values must be finite and ordered, and no tool exposes
row limits or SQL-like controls. These checks narrow inputs only; the repository
layer remains independently fixed and parameterized.

`get_node_profile` selects one family per call (`asn`, `geolocation`,
`interfaces`, or `links`) rather than multiplying independent one-to-many
relations. Omitted `include` means `asn`.

Further schema facts are in the approved
[implementation plan](../plans/2026-08-19-mcp-server-revised.md#data-access-model).

Do not document database credentials or connection strings here.

Generated datasets are operational artifacts, not database snapshots managed
by this service. A completed CSV is atomically published mode `0640` in the
shared output directory and remains there until an operator removes it. Disk
capacity and retention therefore remain deployment responsibilities.
