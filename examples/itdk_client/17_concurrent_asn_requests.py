"""Send several ASN requests concurrently over one initialized session."""

import argparse
import asyncio

from common import call_tool, open_session


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asns", nargs="*", type=int, default=[64500, 64501, 64502])
    args = parser.parse_args()
    async with open_session() as session:
        await asyncio.gather(*(call_tool(session, "find_nodes_by_asn", {"asn": asn}) for asn in args.asns))


if __name__ == "__main__":
    asyncio.run(main())
