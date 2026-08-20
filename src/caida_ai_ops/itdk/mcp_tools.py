"""Strict MCP schemas and dispatch for the seven approved repository operations."""

from __future__ import annotations

import ipaddress
import logging
import math
from collections.abc import Callable
from functools import partial
from typing import Any, Final, cast

import anyio
from jsonschema import Draft202012Validator, FormatChecker
from mcp import types

from .data_access import CsvResult, NodeProfileInclude, Repositories

LOGGER = logging.getLogger(__name__)
MAX_IDENTIFIER_LENGTH: Final = 255
MAX_HOSTNAME_LENGTH: Final = 253
MIN_HOSTNAME_PREFIX_LENGTH: Final = 3
MAX_ASN: Final = 9_223_372_036_854_775_807

IDENTIFIER_SCHEMA: Final[dict[str, Any]] = {
    "type": "string",
    "minLength": 1,
    "maxLength": MAX_IDENTIFIER_LENGTH,
    "pattern": r"^(?=.*\S)[^\x00-\x1f\x7f]+$",
}
HOSTNAME_SCHEMA: Final[dict[str, Any]] = {
    "type": "string",
    "minLength": 1,
    "maxLength": MAX_HOSTNAME_LENGTH,
    "pattern": r"^[!-~]+$",
}
HOSTNAME_PREFIX_SCHEMA: Final[dict[str, Any]] = {
    **HOSTNAME_SCHEMA,
    "minLength": MIN_HOSTNAME_PREFIX_LENGTH,
}
IP_SCHEMA: Final[dict[str, Any]] = {
    "type": "string",
    "anyOf": [{"format": "ipv4"}, {"format": "ipv6"}],
}
OUTPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["file_path", "row_count", "columns"],
    "properties": {
        "file_path": {"type": "string"},
        "row_count": {"type": "integer", "minimum": 0},
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "type"],
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["string", "inet", "int32", "int64", "float64"],
                    },
                },
            },
        },
    },
}


def _object_schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    one_of: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        schema["required"] = required
    if one_of:
        schema["oneOf"] = one_of
    return schema


TOOL_SCHEMAS: Final[dict[str, dict[str, Any]]] = {
    "get_node_profile": _object_schema(
        {
            "node_id": IDENTIFIER_SCHEMA,
            "include": {
                "type": "string",
                "enum": ["asn", "geolocation", "interfaces", "links"],
                "default": "asn",
            },
        },
        required=["node_id"],
    ),
    "find_nodes_by_asn": _object_schema(
        {"asn": {"type": "integer", "minimum": 1, "maximum": MAX_ASN}},
        required=["asn"],
    ),
    "search_nodes_by_geolocation": _object_schema(
        {
            "country": {
                "type": "string",
                "minLength": 2,
                "maxLength": 2,
                "pattern": "^[A-Z]{2}$",
            },
            "longitude_min": {"type": "number", "minimum": -180, "maximum": 180},
            "longitude_max": {"type": "number", "minimum": -180, "maximum": 180},
            "latitude_min": {"type": "number", "minimum": -90, "maximum": 90},
            "latitude_max": {"type": "number", "minimum": -90, "maximum": 90},
        },
        required=["country"],
    ),
    "get_link_endpoints": _object_schema({"link_id": IDENTIFIER_SCHEMA}, required=["link_id"]),
    "find_links_for_node": _object_schema({"node_id": IDENTIFIER_SCHEMA}, required=["node_id"]),
    "get_transit_interfaces": _object_schema(
        {"node_id": IDENTIFIER_SCHEMA, "link_id": IDENTIFIER_SCHEMA},
        one_of=[{"required": ["node_id"]}, {"required": ["link_id"]}],
    ),
    "lookup_router_hostnames": _object_schema(
        {
            "ip": IP_SCHEMA,
            "hostname_exact": HOSTNAME_SCHEMA,
            "hostname_prefix": HOSTNAME_PREFIX_SCHEMA,
        },
        one_of=[
            {"required": ["ip"]},
            {"required": ["hostname_exact"]},
            {"required": ["hostname_prefix"]},
        ],
    ),
}

TOOL_DESCRIPTIONS: Final[dict[str, str]] = {
    "get_node_profile": "Write one approved profile family for a node to CSV.",
    "find_nodes_by_asn": "Write nodes assigned to one ASN to CSV.",
    "search_nodes_by_geolocation": ("Write nodes in one country and optional coordinate bounds to CSV."),
    "get_link_endpoints": "Write every endpoint for one link to CSV.",
    "find_links_for_node": "Write link endpoints containing one node to CSV.",
    "get_transit_interfaces": ("Write transit interfaces selected by exactly one node or link to CSV."),
    "lookup_router_hostnames": (
        "Write router hostnames selected by exactly one IP, exact name, or prefix to CSV."
    ),
}


class SafeToolError(Exception):
    """An intentionally content-free stable MCP tool error."""


async def execute_tool_call(
    repositories: Repositories,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any] | types.CallToolResult:
    """Validate, dispatch, and safely map one SDK tool call."""
    try:
        _validate_input(name, arguments)
        result = await _dispatch(repositories, name, arguments)
        return serialize_csv_result(result)
    except SafeToolError:
        LOGGER.warning(
            "tool input rejected",
            extra={
                "event": "tool_error",
                "context": {
                    "code": "INVALID_ARGUMENT",
                    "tool": name if name in TOOL_SCHEMAS else "unknown",
                },
            },
        )
        return _error_result("INVALID_ARGUMENT")
    except Exception as exc:
        LOGGER.error(
            "tool execution failed",
            extra={
                "event": "tool_error",
                "context": {
                    "code": "INTERNAL_ERROR",
                    "tool": name if name in TOOL_SCHEMAS else "unknown",
                    "category": type(exc).__name__,
                },
            },
        )
        return _error_result("INTERNAL_ERROR")


def serialize_csv_result(result: CsvResult) -> dict[str, Any]:
    """Return only the concise, ordered metadata envelope promised to clients."""
    return {
        "file_path": str(result.file_path),
        "row_count": result.row_count,
        "columns": [{"name": column.name, "type": column.type} for column in result.columns],
    }


def _error_result(code: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=code)],
        is_error=True,
    )


def _validate_input(name: str, arguments: dict[str, Any]) -> None:
    schema = TOOL_SCHEMAS.get(name)
    if schema is None:
        raise SafeToolError("INVALID_ARGUMENT")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    if next(validator.iter_errors(arguments), None) is not None:
        raise SafeToolError("INVALID_ARGUMENT")

    if name == "search_nodes_by_geolocation":
        coordinate_names = (
            "longitude_min",
            "longitude_max",
            "latitude_min",
            "latitude_max",
        )
        if any(
            key in arguments and not math.isfinite(cast(float, arguments[key])) for key in coordinate_names
        ):
            raise SafeToolError("INVALID_ARGUMENT")
        if _minimum_exceeds_maximum(arguments, "longitude_min", "longitude_max"):
            raise SafeToolError("INVALID_ARGUMENT")
        if _minimum_exceeds_maximum(arguments, "latitude_min", "latitude_max"):
            raise SafeToolError("INVALID_ARGUMENT")

    if name == "lookup_router_hostnames" and "ip" in arguments:
        try:
            ipaddress.ip_address(cast(str, arguments["ip"]))
        except ValueError:
            raise SafeToolError("INVALID_ARGUMENT") from None


def _minimum_exceeds_maximum(arguments: dict[str, Any], minimum: str, maximum: str) -> bool:
    return (
        minimum in arguments
        and maximum in arguments
        and cast(float, arguments[minimum]) > cast(float, arguments[maximum])
    )


async def _dispatch(
    repositories: Repositories,
    name: str,
    arguments: dict[str, Any],
) -> CsvResult:
    operation: Callable[[], CsvResult]
    if name == "get_node_profile":
        operation = partial(
            repositories.nodes.get_node_profile,
            cast(str, arguments["node_id"]),
            NodeProfileInclude(cast(str, arguments.get("include", "asn"))),
        )
    elif name == "find_nodes_by_asn":
        operation = partial(repositories.nodes.find_nodes_by_asn, cast(int, arguments["asn"]))
    elif name == "search_nodes_by_geolocation":
        operation = partial(
            repositories.nodes.search_nodes_by_geolocation,
            cast(str, arguments["country"]),
            longitude_min=cast(float | None, arguments.get("longitude_min")),
            longitude_max=cast(float | None, arguments.get("longitude_max")),
            latitude_min=cast(float | None, arguments.get("latitude_min")),
            latitude_max=cast(float | None, arguments.get("latitude_max")),
        )
    elif name == "get_link_endpoints":
        operation = partial(repositories.links.get_link_endpoints, cast(str, arguments["link_id"]))
    elif name == "find_links_for_node":
        operation = partial(repositories.links.find_links_for_node, cast(str, arguments["node_id"]))
    elif name == "get_transit_interfaces":
        operation = partial(
            repositories.transit.get_transit_interfaces,
            node_id=cast(str | None, arguments.get("node_id")),
            link_id=cast(str | None, arguments.get("link_id")),
        )
    elif name == "lookup_router_hostnames":
        operation = partial(
            repositories.hostnames.lookup_router_hostnames,
            ip=cast(str | None, arguments.get("ip")),
            hostname_exact=cast(str | None, arguments.get("hostname_exact")),
            hostname_prefix=cast(str | None, arguments.get("hostname_prefix")),
        )
    else:
        raise SafeToolError("INVALID_ARGUMENT")
    return await anyio.to_thread.run_sync(operation)
