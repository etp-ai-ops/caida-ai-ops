"""Initialize MCP and list the seven advertised tools."""

import asyncio

from common import open_session


async def main() -> None:
    async with open_session() as session:
        result = await session.list_tools()
        for tool in result.tools:
            print(f"{tool.name}: {tool.description}")


if __name__ == "__main__":
    asyncio.run(main())
