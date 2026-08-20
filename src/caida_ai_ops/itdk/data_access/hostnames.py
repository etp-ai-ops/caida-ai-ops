"""Fixed router-hostname lookup queries."""

from __future__ import annotations

from psycopg import sql

from .query import FixedQueryExecutor, Query
from .result_writer import Column, CsvResult

_HOSTNAME_COLUMNS = (Column("ip", "inet"), Column("hostname", "string"))

_BY_IP = Query(
    sql.SQL("""
        SELECT ip, hostname
        FROM caida_itdk.itdk_router_hostnames
        WHERE ip = %s::inet
        ORDER BY ip, hostname NULLS FIRST
    """),
    _HOSTNAME_COLUMNS,
    "lookup_router_hostnames_by_ip",
)

_BY_EXACT_HOSTNAME = Query(
    sql.SQL("""
        SELECT ip, hostname
        FROM caida_itdk.itdk_router_hostnames
        WHERE hostname = %s
        ORDER BY hostname, ip
    """),
    _HOSTNAME_COLUMNS,
    "lookup_router_hostnames_exact",
)

_BY_HOSTNAME_PREFIX = Query(
    sql.SQL(r"""
        SELECT ip, hostname
        FROM caida_itdk.itdk_router_hostnames
        WHERE hostname LIKE %s ESCAPE '\'
        ORDER BY hostname, ip
    """),
    _HOSTNAME_COLUMNS,
    "lookup_router_hostnames_prefix",
)


class HostnameRepository:
    def __init__(self, executor: FixedQueryExecutor) -> None:
        self._executor = executor

    def lookup_router_hostnames(
        self,
        *,
        ip: str | None = None,
        hostname_exact: str | None = None,
        hostname_prefix: str | None = None,
    ) -> CsvResult:
        supplied = sum(value is not None for value in (ip, hostname_exact, hostname_prefix))
        if supplied != 1:
            raise ValueError("exactly one hostname lookup input is required")
        if ip is not None:
            return self._executor.execute(_BY_IP, (ip,))
        if hostname_exact is not None:
            return self._executor.execute(_BY_EXACT_HOSTNAME, (hostname_exact,))
        assert hostname_prefix is not None
        escaped_prefix = hostname_prefix.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        return self._executor.execute(_BY_HOSTNAME_PREFIX, (escaped_prefix + "%",))
