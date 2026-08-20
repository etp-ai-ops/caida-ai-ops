"""The combined MCP catalog must preserve every migrated capability."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_unified_server_exposes_all_tool_families() -> None:
    from caida_ai_ops.server import mcp

    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}

    assert len(names) == 43
    assert {
        "ping_ark",
        "enrich_ark_result",
        "as_rank_get_customer_cone",
        "itdk_geo_adjacent_links",
        "get_node_profile",
        "lookup_router_hostnames",
    } <= names
