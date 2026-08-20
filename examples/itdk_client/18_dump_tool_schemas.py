"""Print the complete JSON schemas advertised by the server."""

import asyncio
import json

from common import open_session


async def main() -> None:
    async with open_session() as session:
        result = await session.list_tools()
        for tool in result.tools:
            dumped = tool.model_dump(mode="json", by_alias=True)
            print(json.dumps(dumped, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
