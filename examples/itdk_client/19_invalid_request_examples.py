"""Send intentionally invalid calls and display safe error responses."""

import asyncio
import json
from typing import Any

from common import open_session


async def main() -> None:
    cases: list[tuple[str, dict[str, Any]]] = [
        ("find_nodes_by_asn", {"asn": 0}),
        ("search_nodes_by_geolocation", {"country": "usa"}),
        ("get_transit_interfaces", {}),
        ("get_transit_interfaces", {"node_id": "N1967", "link_id": "L1"}),
        ("lookup_router_hostnames", {}),
        ("lookup_router_hostnames", {"hostname_prefix": "ab"}),
        ("get_node_profile", {"node_id": "N1967", "limit": 10}),
    ]
    async with open_session() as session:
        for name, arguments in cases:
            result = await session.call_tool(name, arguments)
            dumped = result.model_dump(mode="json", by_alias=True)
            print(f"\n{name} {json.dumps(arguments, sort_keys=True)}")
            print(json.dumps(dumped, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
