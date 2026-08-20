"""Look up router IP rows using one exact hostname."""

import argparse
import asyncio

from common import call_tool, open_session, preview_argument


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hostname", nargs="?", default="router.example.net")
    preview_argument(parser)
    args = parser.parse_args()
    async with open_session() as session:
        await call_tool(
            session,
            "lookup_router_hostnames",
            {"hostname_exact": args.hostname},
            preview=args.preview,
        )


if __name__ == "__main__":
    asyncio.run(main())
