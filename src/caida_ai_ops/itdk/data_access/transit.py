"""Fixed transit-interface queries.

There is no `v_itdk_ifaces_transit` view on the supplied database; only the
four base tables in the plan's data-access model exist. A transit interface is
derived directly from `itdk_link_endpoints.endpoint_token`: CAIDA encodes an
endpoint's resolved interface as `<node_id>:<ip>` when traceroute captured one,
and as a bare `node_id` otherwise. There is no per-interface flag data in the
supplied schema, so `flags` is always NULL; the column is kept for output
contract stability.
"""

from __future__ import annotations

from psycopg import sql

from .query import FixedQueryExecutor, Query
from .result_writer import Column, CsvResult

_TRANSIT_COLUMNS = (
    Column("ip", "inet"),
    Column("node_id", "string"),
    Column("link_id", "string"),
    Column("flags", "string"),
)

_TRANSIT_IP_EXPRESSION = "substring(endpoint_token FROM position(':' IN endpoint_token) + 1)::inet"

_BY_NODE = Query(
    sql.SQL(f"""
        SELECT {_TRANSIT_IP_EXPRESSION} AS ip, node_id, link_id, NULL::text AS flags
        FROM caida_itdk.itdk_link_endpoints
        WHERE node_id = %s
          AND endpoint_token LIKE '%%:%%'
        ORDER BY node_id, link_id, ip, flags NULLS FIRST
    """),
    _TRANSIT_COLUMNS,
    "get_transit_interfaces_by_node",
)

_BY_LINK = Query(
    sql.SQL(f"""
        SELECT {_TRANSIT_IP_EXPRESSION} AS ip, node_id, link_id, NULL::text AS flags
        FROM caida_itdk.itdk_link_endpoints
        WHERE link_id = %s
          AND endpoint_token LIKE '%%:%%'
        ORDER BY link_id, node_id, ip, flags NULLS FIRST
    """),
    _TRANSIT_COLUMNS,
    "get_transit_interfaces_by_link",
)


class TransitRepository:
    def __init__(self, executor: FixedQueryExecutor) -> None:
        self._executor = executor

    def get_transit_interfaces(
        self,
        *,
        node_id: str | None = None,
        link_id: str | None = None,
    ) -> CsvResult:
        if (node_id is None) == (link_id is None):
            raise ValueError("exactly one of node_id or link_id is required")
        if node_id is not None:
            return self._executor.execute(_BY_NODE, (node_id,))
        return self._executor.execute(_BY_LINK, (link_id,))
