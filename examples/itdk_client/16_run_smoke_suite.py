"""Run representative calls for every tool family in one MCP session."""

import argparse
import asyncio

from common import call_tool, open_session


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", default="N1967")
    parser.add_argument("--link-id", default="L1")
    parser.add_argument("--asn", type=int, default=64500)
    parser.add_argument("--country", default="US")
    parser.add_argument("--ip", default="192.0.2.1")
    parser.add_argument("--hostname", default="router.example.net")
    parser.add_argument("--hostname-prefix", default="router")
    parser.add_argument("--preview", type=int, default=0)
    args = parser.parse_args()
    cases = [
        ("get_node_profile", {"node_id": args.node_id}),
        ("find_nodes_by_asn", {"asn": args.asn}),
        ("search_nodes_by_geolocation", {"country": args.country}),
        ("get_link_endpoints", {"link_id": args.link_id}),
        ("find_links_for_node", {"node_id": args.node_id}),
        ("get_transit_interfaces", {"node_id": args.node_id}),
        ("lookup_router_hostnames", {"ip": args.ip}),
        ("lookup_router_hostnames", {"hostname_exact": args.hostname}),
        ("lookup_router_hostnames", {"hostname_prefix": args.hostname_prefix}),
    ]
    async with open_session() as session:
        for tool_name, arguments in cases:
            await call_tool(session, tool_name, arguments, preview=args.preview)


if __name__ == "__main__":
    asyncio.run(main())
