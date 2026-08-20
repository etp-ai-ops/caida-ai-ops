"""Look up router rows using a literal hostname prefix."""

import argparse
import asyncio

from common import call_tool, open_session, preview_argument


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", nargs="?", default="router")
    preview_argument(parser)
    args = parser.parse_args()
    async with open_session() as session:
        await call_tool(
            session,
            "lookup_router_hostnames",
            {"hostname_prefix": args.prefix},
            preview=args.preview,
        )


if __name__ == "__main__":
    asyncio.run(main())
