"""Register the generic ITDK repository tools on the unified stdio server."""

from __future__ import annotations

import atexit
import os
from threading import Lock
from typing import Any

from mcp import types
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .itdk.config import Settings
from .itdk.data_access import DatabasePool, Repositories
from .itdk.mcp_tools import TOOL_SCHEMAS, execute_tool_call

_runtime_lock = Lock()
_runtime: tuple[DatabasePool, Repositories] | None = None
_shutdown_registered = False

READ_ONLY = types.ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _repositories() -> Repositories:
    """Create the protected pool lazily so tool discovery needs no credentials."""
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                values = dict(os.environ)
                # Authentication is a transport concern. The stdio transport is
                # process-local, but Settings still owns the shared DB validation.
                values.setdefault("MCP_MASTER_KEY", "stdio-transport-does-not-use-a-key")
                settings = Settings.from_env(values)
                settings.prepare_output_dir()
                pool = DatabasePool.from_settings(settings)
                pool.open()
                _runtime = (pool, Repositories.from_settings(settings, pool))
                _register_shutdown()
    return _runtime[1]


def configure_itdk_runtime(settings: Settings, pool: DatabasePool | None = None) -> DatabasePool:
    """Bind the runtime that an HTTP application owns and closes."""
    global _runtime
    active_pool = pool or DatabasePool.from_settings(settings)
    _runtime = (active_pool, Repositories.from_settings(settings, active_pool))
    _register_shutdown()
    return active_pool


def _register_shutdown() -> None:
    global _shutdown_registered
    if not _shutdown_registered:
        atexit.register(_close_runtime)
        _shutdown_registered = True


def _close_runtime() -> None:
    if _runtime is not None:
        _runtime[0].close()


def _arguments(**values: Any) -> dict[str, Any]:
    return {name: value for name, value in values.items() if value is not None}


async def _execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await execute_tool_call(_repositories(), name, arguments)
    if isinstance(result, types.CallToolResult):
        code = next(
            (item.text for item in result.content if isinstance(item, types.TextContent)),
            "INTERNAL_ERROR",
        )
        raise ToolError(code)
    return result


def register_itdk_tools(server: MCPServer[Any]) -> None:
    """Attach the seven safe, generic ITDK tools to ``server``."""

    @server.tool(annotations=READ_ONLY)
    async def get_node_profile(node_id: str, include: str = "asn") -> dict[str, Any]:
        """Write one approved profile family for an ITDK node to CSV."""
        return await _execute_tool("get_node_profile", _arguments(node_id=node_id, include=include))

    @server.tool(annotations=READ_ONLY)
    async def find_nodes_by_asn(asn: int) -> dict[str, Any]:
        """Write ITDK nodes assigned to one ASN to CSV."""
        return await _execute_tool("find_nodes_by_asn", {"asn": asn})

    @server.tool(annotations=READ_ONLY)
    async def search_nodes_by_geolocation(
        country: str,
        longitude_min: float | None = None,
        longitude_max: float | None = None,
        latitude_min: float | None = None,
        latitude_max: float | None = None,
    ) -> dict[str, Any]:
        """Write ITDK nodes in a country and optional coordinate bounds to CSV."""
        return await _execute_tool(
            "search_nodes_by_geolocation",
            _arguments(
                country=country,
                longitude_min=longitude_min,
                longitude_max=longitude_max,
                latitude_min=latitude_min,
                latitude_max=latitude_max,
            ),
        )

    @server.tool(annotations=READ_ONLY)
    async def get_link_endpoints(link_id: str) -> dict[str, Any]:
        """Write every endpoint for one ITDK link to CSV."""
        return await _execute_tool("get_link_endpoints", {"link_id": link_id})

    @server.tool(annotations=READ_ONLY)
    async def find_links_for_node(node_id: str) -> dict[str, Any]:
        """Write ITDK link endpoints containing one node to CSV."""
        return await _execute_tool("find_links_for_node", {"node_id": node_id})

    @server.tool(annotations=READ_ONLY)
    async def get_transit_interfaces(
        node_id: str | None = None,
        link_id: str | None = None,
    ) -> dict[str, Any]:
        """Write transit interfaces selected by exactly one node or link to CSV."""
        return await _execute_tool("get_transit_interfaces", _arguments(node_id=node_id, link_id=link_id))

    @server.tool(annotations=READ_ONLY)
    async def lookup_router_hostnames(
        ip: str | None = None,
        hostname_exact: str | None = None,
        hostname_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Write router hostnames selected by exactly one IP, exact name, or prefix to CSV."""
        return await _execute_tool(
            "lookup_router_hostnames",
            _arguments(ip=ip, hostname_exact=hostname_exact, hostname_prefix=hostname_prefix),
        )

    # MCPServer v2 generates a useful signature schema but does not mark
    # function argument models as closed. Publish the audited contract schemas;
    # execute_tool_call independently validates the same objects at runtime.
    for name, schema in TOOL_SCHEMAS.items():
        tool = server._tool_manager.get_tool(name)
        if tool is None:  # pragma: no cover - registration invariant
            raise RuntimeError(f"ITDK tool registration failed: {name}")
        tool.parameters = schema
