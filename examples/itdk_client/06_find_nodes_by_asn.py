"""Find all nodes assigned to one ASN."""

import argparse
import asyncio

from common import call_tool, open_session, preview_argument


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asn", nargs="?", default=64500, type=int)
    preview_argument(parser, default=10)
    args = parser.parse_args()
    async with open_session() as session:
        await call_tool(session, "find_nodes_by_asn", {"asn": args.asn}, preview=args.preview)


if __name__ == "__main__":
    asyncio.run(main())
