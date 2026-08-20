"""Fixed link endpoint queries."""

from __future__ import annotations

from psycopg import sql

from .query import FixedQueryExecutor, Query
from .result_writer import Column, CsvResult

_LINK_COLUMNS = (
    Column("link_id", "string"),
    Column("endpoint_ordinal", "int32"),
    Column("endpoint_token", "string"),
    Column("node_id", "string"),
)

_GET_LINK_ENDPOINTS = Query(
    sql.SQL("""
        SELECT link_id, endpoint_ordinal, endpoint_token, node_id
        FROM caida_itdk.itdk_link_endpoints
        WHERE link_id = %s
        ORDER BY endpoint_ordinal
    """),
    _LINK_COLUMNS,
    "get_link_endpoints",
)

_FIND_LINKS_FOR_NODE = Query(
    sql.SQL("""
        SELECT link_id, endpoint_ordinal, endpoint_token, node_id
        FROM caida_itdk.itdk_link_endpoints
        WHERE node_id = %s
        ORDER BY link_id, endpoint_ordinal
    """),
    _LINK_COLUMNS,
    "find_links_for_node",
)


class LinkRepository:
    def __init__(self, executor: FixedQueryExecutor) -> None:
        self._executor = executor

    def get_link_endpoints(self, link_id: str) -> CsvResult:
        return self._executor.execute(_GET_LINK_ENDPOINTS, (link_id,))

    def find_links_for_node(self, node_id: str) -> CsvResult:
        return self._executor.execute(_FIND_LINKS_FOR_NODE, (node_id,))
