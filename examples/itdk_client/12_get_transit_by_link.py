"""Get transit-interface rows selected by link ID."""

import argparse
import asyncio

from common import call_tool, open_session, preview_argument


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("link_id", nargs="?", default="L1")
    preview_argument(parser)
    args = parser.parse_args()
    async with open_session() as session:
        await call_tool(
            session,
            "get_transit_interfaces",
            {"link_id": args.link_id},
            preview=args.preview,
        )


if __name__ == "__main__":
    asyncio.run(main())
