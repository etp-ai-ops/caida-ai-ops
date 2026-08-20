"""Fixed node profile, ASN, and geolocation queries."""

from __future__ import annotations

from enum import StrEnum

from psycopg import sql

from .query import FixedQueryExecutor, Query
from .result_writer import Column, CsvResult
from .transit import _TRANSIT_IP_EXPRESSION


class NodeProfileInclude(StrEnum):
    ASN = "asn"
    GEOLOCATION = "geolocation"
    INTERFACES = "interfaces"
    LINKS = "links"


_PROFILE_QUERIES = {
    NodeProfileInclude.ASN: Query(
        sql.SQL("""
            SELECT node_id, asn, method
            FROM caida_itdk.itdk_node_as
            WHERE node_id = %s
            ORDER BY node_id, asn
        """),
        (Column("node_id", "string"), Column("asn", "int64"), Column("method", "string")),
        "get_node_profile_asn",
    ),
    NodeProfileInclude.GEOLOCATION: Query(
        sql.SQL("""
            SELECT node_id, continent, country, region, city, latitude, longitude, method
            FROM caida_itdk.itdk_node_geolocation
            WHERE node_id = %s
            ORDER BY node_id
        """),
        (
            Column("node_id", "string"),
            Column("continent", "string"),
            Column("country", "string"),
            Column("region", "string"),
            Column("city", "string"),
            Column("latitude", "float64"),
            Column("longitude", "float64"),
            Column("method", "string"),
        ),
        "get_node_profile_geolocation",
    ),
    NodeProfileInclude.INTERFACES: Query(
        # Derived from itdk_link_endpoints; see transit.py for why there is no
        # v_itdk_ifaces_transit view on the supplied database.
        sql.SQL(f"""
            SELECT {_TRANSIT_IP_EXPRESSION} AS ip, node_id, link_id, NULL::text AS flags
            FROM caida_itdk.itdk_link_endpoints
            WHERE node_id = %s
              AND endpoint_token LIKE '%%:%%'
            ORDER BY node_id, link_id, ip, flags NULLS FIRST
        """),
        (
            Column("ip", "inet"),
            Column("node_id", "string"),
            Column("link_id", "string"),
            Column("flags", "string"),
        ),
        "get_node_profile_interfaces",
    ),
    NodeProfileInclude.LINKS: Query(
        sql.SQL("""
            SELECT link_id, endpoint_ordinal, endpoint_token, node_id
            FROM caida_itdk.itdk_link_endpoints
            WHERE node_id = %s
            ORDER BY link_id, endpoint_ordinal
        """),
        (
            Column("link_id", "string"),
            Column("endpoint_ordinal", "int32"),
            Column("endpoint_token", "string"),
            Column("node_id", "string"),
        ),
        "get_node_profile_links",
    ),
}

_FIND_BY_ASN = Query(
    sql.SQL("""
        SELECT node_id, asn, method
        FROM caida_itdk.itdk_node_as
        WHERE asn = %s
        ORDER BY node_id
    """),
    (Column("node_id", "string"), Column("asn", "int64"), Column("method", "string")),
    "find_nodes_by_asn",
)

_SEARCH_GEOLOCATION = Query(
    sql.SQL("""
        SELECT node_id, continent, country, region, city, latitude, longitude, method
        FROM caida_itdk.itdk_node_geolocation
        WHERE country = %s
          AND (%s::double precision IS NULL OR longitude >= %s::double precision)
          AND (%s::double precision IS NULL OR longitude <= %s::double precision)
          AND (%s::double precision IS NULL OR latitude >= %s::double precision)
          AND (%s::double precision IS NULL OR latitude <= %s::double precision)
        ORDER BY country, longitude NULLS FIRST, latitude NULLS FIRST, node_id
    """),
    _PROFILE_QUERIES[NodeProfileInclude.GEOLOCATION].columns,
    "search_nodes_by_geolocation",
)


class NodeRepository:
    def __init__(self, executor: FixedQueryExecutor) -> None:
        self._executor = executor

    def get_node_profile(
        self,
        node_id: str,
        include: NodeProfileInclude = NodeProfileInclude.ASN,
    ) -> CsvResult:
        """Write one approved profile family for a node; ASN is the stable default."""
        return self._executor.execute(_PROFILE_QUERIES[include], (node_id,))

    def find_nodes_by_asn(self, asn: int) -> CsvResult:
        return self._executor.execute(_FIND_BY_ASN, (asn,))

    def search_nodes_by_geolocation(
        self,
        country: str,
        *,
        longitude_min: float | None = None,
        longitude_max: float | None = None,
        latitude_min: float | None = None,
        latitude_max: float | None = None,
    ) -> CsvResult:
        parameters = (
            country,
            longitude_min,
            longitude_min,
            longitude_max,
            longitude_max,
            latitude_min,
            latitude_min,
            latitude_max,
            latitude_max,
        )
        return self._executor.execute(_SEARCH_GEOLOCATION, parameters)
