# 0006 — Derive transit interfaces from endpoint tokens

**Status:** Accepted
**Date:** 2026-08-19

## Context

The implementation plan assumed a `caida_itdk.v_itdk_ifaces_transit` view
would exist on the supplied database, backing `get_transit_interfaces` and
`get_node_profile`'s `interfaces` family. Running against the real database
at `db.nids.caida.org` showed only four base tables; the view does not exist,
and the application's read-only role cannot create it. Every call to the
affected tools failed with a Postgres `UndefinedTable` error, surfaced
correctly but unhelpfully as a generic `INTERNAL_ERROR`.

Inspecting `itdk_link_endpoints.endpoint_token` across all 14.2M rows on the
real database showed a consistent, undocumented convention: ~23% of tokens
are `<node_id>:<ip>` rather than a bare `node_id`, always with a matching
`node_id` prefix and never more than one colon. This is CAIDA's encoding for
a link endpoint whose transit interface IP was resolved by traceroute.

## Decision

Derive transit-interface rows directly from `itdk_link_endpoints` instead of
querying the assumed view:

```sql
SELECT
    substring(endpoint_token FROM position(':' IN endpoint_token) + 1)::inet AS ip,
    node_id,
    link_id,
    NULL::text AS flags
FROM caida_itdk.itdk_link_endpoints
WHERE node_id = %s  -- or link_id = %s
  AND endpoint_token LIKE '%:%'
ORDER BY node_id, link_id, ip, flags NULLS FIRST
```

`flags` has no source in the supplied schema and is always `NULL`; the column
stays in the output contract for stability rather than breaking the
documented shape. The output contract, tool names, and input schemas are
unchanged.

## Consequences

`get_transit_interfaces` and the `interfaces` profile family now work against
the real database with no additional grant, view, or DBA action required.
"Transit interface" now means, precisely, a link endpoint whose IP was
captured by traceroute; nodes with no resolved interface on a given endpoint
are correctly excluded rather than erroring. If CAIDA later provisions a real
`v_itdk_ifaces_transit` view with richer flag data, this derivation should be
revisited and can be swapped back with no contract change.
