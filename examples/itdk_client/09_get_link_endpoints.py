"""Get every endpoint row for one exact link."""

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
            "get_link_endpoints",
            {"link_id": args.link_id},
            preview=args.preview,
        )


if __name__ == "__main__":
    asyncio.run(main())
