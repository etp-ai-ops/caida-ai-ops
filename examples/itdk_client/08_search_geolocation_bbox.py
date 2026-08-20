"""Search a country's geolocation rows inside a bounding box."""

import argparse
import asyncio

from common import call_tool, open_session, preview_argument


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default="US")
    parser.add_argument("--longitude-min", type=float, default=-125.0)
    parser.add_argument("--longitude-max", type=float, default=-115.0)
    parser.add_argument("--latitude-min", type=float, default=30.0)
    parser.add_argument("--latitude-max", type=float, default=40.0)
    preview_argument(parser, default=10)
    args = parser.parse_args()
    arguments = {
        "country": args.country,
        "longitude_min": args.longitude_min,
        "longitude_max": args.longitude_max,
        "latitude_min": args.latitude_min,
        "latitude_max": args.latitude_max,
    }
    async with open_session() as session:
        await call_tool(session, "search_nodes_by_geolocation", arguments, preview=args.preview)


if __name__ == "__main__":
    asyncio.run(main())
