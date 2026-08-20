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

Configuration (``MCP_MASTER_KEY``, ``DATABASE_URL`` or the ``DB_*`` variables,
and ``OUTPUT_DIR``) is read from the environment, and from a ``.env`` file if
one is found -- so credentials live in a gitignored file rather than in an MCP
client config that is easy to commit by accident. Real environment variables
win over the file. Without configuration the server fails at startup naming
what is missing, rather than starting and failing on every tool call.
"""

from __future__ import annotations

import os
from pathlib import Path

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


def _load_env_file() -> str | None:
    """Load a ``.env`` if present, without overriding the real environment.

    An MCP client config is a JSON file that tends to end up in version
    control, so putting a database password in its ``env`` block is a leak
    waiting to happen. Reading a gitignored ``.env`` instead keeps the client
    config free of secrets. ``ITDK_ENV_FILE`` names the file explicitly;
    otherwise it is searched for from the working directory upward.

    ``override=False`` so anything already exported still wins -- useful for
    containers and CI, where configuration arrives as real variables.
    """
    from dotenv import find_dotenv, load_dotenv

    explicit = os.environ.get("ITDK_ENV_FILE")
    path = explicit or find_dotenv(usecwd=True)
    if path and Path(path).is_file():
        load_dotenv(path, override=False)
        return path
    if explicit:
        raise FileNotFoundError(f"ITDK_ENV_FILE points at a file that does not exist: {explicit}")
    return None


def main() -> None:
    """Console-script entry point (``caida-itdk-mcp``).

    Settings are validated before serving so a misconfigured deployment fails
    immediately and visibly, instead of connecting successfully and erroring on
    every tool call.
    """
    _load_env_file()
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
