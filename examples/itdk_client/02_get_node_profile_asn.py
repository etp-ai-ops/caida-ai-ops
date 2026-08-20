"""Request the default ASN profile family for one node."""

import argparse
import asyncio

from common import call_tool, open_session, preview_argument


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_id", nargs="?", default="N1967")
    preview_argument(parser)
    args = parser.parse_args()
    async with open_session() as session:
        await call_tool(session, "get_node_profile", {"node_id": args.node_id}, preview=args.preview)


if __name__ == "__main__":
    asyncio.run(main())
