"""ITDK-only MCP server (stdio).

The unified server in ``server.py`` exposes Ark, AS Rank, and ITDK together.
That is the right default, but it forces one Python interpreter on all three --
and the Ark half needs CAIDA's ``python3-scamper``, a compiled C extension
available only for the interpreter its distribution targets. On a host whose
scamper build and this package's Python floor disagree, the unified server can
only run where both happen to line up.

This entry point serves the ITDK tools alone, so ITDK can run under one
interpreter while Ark runs under another. Register both with an MCP client and
the tools merge into a single session -- the client does not care that they are
separate processes, which is the point of the protocol.

Run:
    caida-itdk-mcp                      # after `pip install .`
    python -m caida_ai_ops.itdk_mcp     # from a source checkout

Requires the ITDK database configuration (``MCP_MASTER_KEY`` and either
``DATABASE_URL`` or the ``DB_*`` variables) -- see ``.env.example``. Without it
the server fails at startup with the missing settings named, rather than
starting and failing on every tool call.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .itdk.config import Settings
from .itdk_stdio import configure_itdk_runtime, register_itdk_tools

mcp = MCPServer(
    name="caida-itdk",
    description=(
        "Read-only queries against CAIDA's ITDK Internet topology data: router "
        "nodes, their ASNs and geolocation, links, transit relationships, and "
        "router hostname lookups."
    ),
)

register_itdk_tools(mcp)


def main() -> None:
    """Console-script entry point (``caida-itdk-mcp``).

    Settings are validated before serving so a misconfigured deployment fails
    immediately and visibly, instead of connecting successfully and erroring on
    every tool call.
    """
    # configure_itdk_runtime builds the pool but does not open it -- the HTTP
    # service opens it from a Starlette lifespan handler. stdio has no lifespan,
    # so open it here, or every tool call fails with PoolClosed.
    pool = configure_itdk_runtime(Settings.from_env())
    pool.open()
    try:
        mcp.run()
    finally:
        pool.close()


if __name__ == "__main__":
    main()
