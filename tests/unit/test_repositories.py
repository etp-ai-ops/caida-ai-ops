from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from caida_ai_ops.itdk.data_access import CsvResult, NodeProfileInclude
from caida_ai_ops.itdk.data_access.hostnames import HostnameRepository
from caida_ai_ops.itdk.data_access.links import LinkRepository
from caida_ai_ops.itdk.data_access.nodes import NodeRepository
from caida_ai_ops.itdk.data_access.query import Query
from caida_ai_ops.itdk.data_access.transit import TransitRepository


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[Query, tuple[object, ...]]] = []

    def execute(self, query: Query, parameters: tuple[object, ...]) -> CsvResult:
        self.calls.append((query, parameters))
        return CsvResult(Path("/output/result.csv"), 0, query.columns)


@pytest.mark.parametrize(
    ("invoke", "parameters", "projection", "ordering"),
    [
        (
            lambda n, _l, _t, _h: n.get_node_profile("N'; DROP TABLE x; --"),
            ("N'; DROP TABLE x; --",),
            ["node_id", "asn", "method"],
            "ORDER BY node_id, asn",
        ),
        (
            lambda n, _l, _t, _h: n.get_node_profile("N1", NodeProfileInclude.GEOLOCATION),
            ("N1",),
            [
                "node_id",
                "continent",
                "country",
                "region",
                "city",
                "latitude",
                "longitude",
                "method",
            ],
            "ORDER BY node_id",
        ),
        (
            lambda n, _l, _t, _h: n.get_node_profile("N1", NodeProfileInclude.INTERFACES),
            ("N1",),
            ["ip", "node_id", "link_id", "flags"],
            "ORDER BY node_id, link_id, ip, flags NULLS FIRST",
        ),
        (
            lambda n, _l, _t, _h: n.get_node_profile("N1", NodeProfileInclude.LINKS),
            ("N1",),
            ["link_id", "endpoint_ordinal", "endpoint_token", "node_id"],
            "ORDER BY link_id, endpoint_ordinal",
        ),
        (
            lambda n, _l, _t, _h: n.find_nodes_by_asn(64500),
            (64500,),
            ["node_id", "asn", "method"],
            "ORDER BY node_id",
        ),
        (
            lambda n, _l, _t, _h: n.search_nodes_by_geolocation(
                "US", longitude_min=-10.0, longitude_max=20.0, latitude_min=30.0, latitude_max=40.0
            ),
            ("US", -10.0, -10.0, 20.0, 20.0, 30.0, 30.0, 40.0, 40.0),
            [
                "node_id",
                "continent",
                "country",
                "region",
                "city",
                "latitude",
                "longitude",
                "method",
            ],
            "ORDER BY country, longitude NULLS FIRST, latitude NULLS FIRST, node_id",
        ),
        (
            lambda _n, link, _t, _h: link.get_link_endpoints("L1"),
            ("L1",),
            ["link_id", "endpoint_ordinal", "endpoint_token", "node_id"],
            "ORDER BY endpoint_ordinal",
        ),
        (
            lambda _n, link, _t, _h: link.find_links_for_node("N1"),
            ("N1",),
            ["link_id", "endpoint_ordinal", "endpoint_token", "node_id"],
            "ORDER BY link_id, endpoint_ordinal",
        ),
        (
            lambda _n, _l, transit, _h: transit.get_transit_interfaces(node_id="N1"),
            ("N1",),
            ["ip", "node_id", "link_id", "flags"],
            "ORDER BY node_id, link_id, ip, flags NULLS FIRST",
        ),
        (
            lambda _n, _l, transit, _h: transit.get_transit_interfaces(link_id="L1"),
            ("L1",),
            ["ip", "node_id", "link_id", "flags"],
            "ORDER BY link_id, node_id, ip, flags NULLS FIRST",
        ),
        (
            lambda _n, _l, _t, host: host.lookup_router_hostnames(ip="192.0.2.1"),
            ("192.0.2.1",),
            ["ip", "hostname"],
            "ORDER BY ip, hostname NULLS FIRST",
        ),
        (
            lambda _n, _l, _t, host: host.lookup_router_hostnames(hostname_exact="r.example"),
            ("r.example",),
            ["ip", "hostname"],
            "ORDER BY hostname, ip",
        ),
        (
            lambda _n, _l, _t, host: host.lookup_router_hostnames(hostname_prefix="r%_\\"),
            (r"r\%\_\\%",),
            ["ip", "hostname"],
            "ORDER BY hostname, ip",
        ),
    ],
)
def test_fixed_selects_bind_every_caller_value(
    invoke: Any,
    parameters: tuple[object, ...],
    projection: list[str],
    ordering: str,
) -> None:
    executor = RecordingExecutor()
    invoke(
        NodeRepository(executor),
        LinkRepository(executor),
        TransitRepository(executor),
        HostnameRepository(executor),
    )
    query, actual_parameters = executor.calls[-1]
    statement = query.statement.as_string()
    assert statement.lstrip().startswith("SELECT ")
    assert statement.count("%s") == len(actual_parameters)
    assert actual_parameters == parameters
    assert [column.name for column in query.columns] == projection
    assert ordering in " ".join(statement.split())
    assert not any(str(value) in statement for value in actual_parameters if value is not None)
    assert " LIMIT " not in f" {' '.join(statement.upper().split())} "


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"node_id": "N1", "link_id": "L1"}],
)
def test_transit_repository_defends_exactly_one_selector(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        TransitRepository(RecordingExecutor()).get_transit_interfaces(**kwargs)


@pytest.mark.parametrize(
    "invoke",
    [
        lambda n, _t: n.get_node_profile("N1", NodeProfileInclude.INTERFACES),
        lambda _n, t: t.get_transit_interfaces(node_id="N1"),
        lambda _n, t: t.get_transit_interfaces(link_id="L1"),
    ],
)
def test_transit_queries_derive_ip_from_endpoint_token(invoke: Any) -> None:
    """No `v_itdk_ifaces_transit` view exists on the supplied database; a transit
    interface's ip is parsed from `itdk_link_endpoints.endpoint_token`, which CAIDA
    encodes as `<node_id>:<ip>` only when a transit interface was resolved."""
    executor = RecordingExecutor()
    invoke(NodeRepository(executor), TransitRepository(executor))
    statement = " ".join(executor.calls[-1][0].statement.as_string().split())
    assert "FROM caida_itdk.itdk_link_endpoints" in statement
    assert "v_itdk_ifaces_transit" not in statement
    assert "endpoint_token LIKE '%%:%%'" in statement
    assert "position(':' IN endpoint_token)" in statement
    assert "NULL::text AS flags" in statement


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"ip": "192.0.2.1", "hostname_exact": "r.example"}],
)
def test_hostname_repository_defends_exactly_one_selector(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        HostnameRepository(RecordingExecutor()).lookup_router_hostnames(**kwargs)
