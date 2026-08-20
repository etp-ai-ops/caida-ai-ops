from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from caida_ai_ops.itdk.mcp_tools import OUTPUT_SCHEMA, TOOL_SCHEMAS, SafeToolError, _validate_input

VALID_ARGUMENTS: dict[str, dict[str, Any]] = {
    "get_node_profile": {"node_id": "N1967"},
    "find_nodes_by_asn": {"asn": 64500},
    "search_nodes_by_geolocation": {"country": "US"},
    "get_link_endpoints": {"link_id": "L1"},
    "find_links_for_node": {"node_id": "N1967"},
    "get_transit_interfaces": {"node_id": "N1967"},
    "lookup_router_hostnames": {"ip": "192.0.2.1"},
}


def test_exact_tool_set_and_strict_object_schemas() -> None:
    assert list(TOOL_SCHEMAS) == list(VALID_ARGUMENTS)
    for schema in TOOL_SCHEMAS.values():
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


@pytest.mark.parametrize(("name", "arguments"), VALID_ARGUMENTS.items())
def test_minimal_valid_inputs(name: str, arguments: dict[str, Any]) -> None:
    _validate_input(name, arguments)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_node_profile", {"node_id": "N1", "include": "links"}),
        ("find_nodes_by_asn", {"asn": 9_223_372_036_854_775_807}),
        (
            "search_nodes_by_geolocation",
            {
                "country": "US",
                "longitude_min": -180,
                "longitude_max": 180,
                "latitude_min": -90,
                "latitude_max": 90,
            },
        ),
        ("get_transit_interfaces", {"link_id": "L1"}),
        ("lookup_router_hostnames", {"hostname_exact": "r.example"}),
        ("lookup_router_hostnames", {"hostname_prefix": "r.e"}),
        ("lookup_router_hostnames", {"ip": "2001:db8::1"}),
    ],
)
def test_boundary_valid_inputs(name: str, arguments: dict[str, Any]) -> None:
    _validate_input(name, arguments)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("unknown", {}),
        ("get_node_profile", {}),
        ("get_node_profile", {"node_id": " "}),
        ("get_node_profile", {"node_id": "N\n1"}),
        ("get_node_profile", {"node_id": "N1", "include": "all"}),
        ("get_node_profile", {"node_id": "N1", "limit": 1}),
        ("find_nodes_by_asn", {"asn": True}),
        ("find_nodes_by_asn", {"asn": 0}),
        ("find_nodes_by_asn", {"asn": 9_223_372_036_854_775_808}),
        ("search_nodes_by_geolocation", {"country": "us"}),
        ("search_nodes_by_geolocation", {"country": "USA"}),
        ("search_nodes_by_geolocation", {"country": "US", "longitude_min": -181}),
        ("search_nodes_by_geolocation", {"country": "US", "latitude_max": 91}),
        ("search_nodes_by_geolocation", {"country": "US", "longitude_min": float("nan")}),
        ("search_nodes_by_geolocation", {"country": "US", "latitude_max": float("inf")}),
        (
            "search_nodes_by_geolocation",
            {"country": "US", "longitude_min": 2, "longitude_max": 1},
        ),
        (
            "search_nodes_by_geolocation",
            {"country": "US", "latitude_min": 2, "latitude_max": 1},
        ),
        ("get_link_endpoints", {"link_id": "x" * 256}),
        ("get_transit_interfaces", {}),
        ("get_transit_interfaces", {"node_id": "N1", "link_id": "L1"}),
        ("lookup_router_hostnames", {}),
        ("lookup_router_hostnames", {"ip": "not-an-ip"}),
        ("lookup_router_hostnames", {"hostname_prefix": "ab"}),
        ("lookup_router_hostnames", {"hostname_exact": "contains space"}),
        (
            "lookup_router_hostnames",
            {"ip": "192.0.2.1", "hostname_exact": "router.example"},
        ),
    ],
)
def test_invalid_and_cross_field_inputs(name: str, arguments: dict[str, Any]) -> None:
    with pytest.raises(SafeToolError, match="INVALID_ARGUMENT"):
        _validate_input(name, arguments)


def test_every_schema_rejects_extra_sql_controls() -> None:
    for name, valid in VALID_ARGUMENTS.items():
        for forbidden in ("sql", "table", "columns", "order", "limit", "cursor"):
            arguments = deepcopy(valid)
            arguments[forbidden] = "attacker-controlled"
            with pytest.raises(SafeToolError):
                _validate_input(name, arguments)


def test_output_schema_is_exact_and_typed() -> None:
    assert OUTPUT_SCHEMA["additionalProperties"] is False
    assert OUTPUT_SCHEMA["required"] == ["file_path", "row_count", "columns"]
    item = OUTPUT_SCHEMA["properties"]["columns"]["items"]
    assert item["additionalProperties"] is False
    assert item["properties"]["type"]["enum"] == [
        "string",
        "inet",
        "int32",
        "int64",
        "float64",
    ]
