"""Look up router hostnames using an IPv4 or IPv6 address."""

import argparse
import asyncio

from common import call_tool, open_session, preview_argument


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ip", nargs="?", default="192.0.2.1")
    preview_argument(parser)
    args = parser.parse_args()
    async with open_session() as session:
        await call_tool(
            session,
            "lookup_router_hostnames",
            {"ip": args.ip},
            preview=args.preview,
        )


if __name__ == "__main__":
    asyncio.run(main())
