"""Search geolocation rows using the required country filter."""

import argparse
import asyncio

from common import call_tool, open_session, preview_argument


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("country", nargs="?", default="US")
    preview_argument(parser, default=10)
    args = parser.parse_args()
    async with open_session() as session:
        await call_tool(
            session,
            "search_nodes_by_geolocation",
            {"country": args.country},
            preview=args.preview,
        )


if __name__ == "__main__":
    asyncio.run(main())
