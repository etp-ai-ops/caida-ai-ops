"""Shared authenticated MCP session and result display helpers."""

from __future__ import annotations

import csv
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from httpx2 import AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_SERVER_OUTPUT_DIR = "/app/outputs"


def required_master_key() -> str:
    key = os.environ.get("MCP_MASTER_KEY", "")
    if not key:
        raise SystemExit("Set MCP_MASTER_KEY to the key used by the running server.")
    return key


@asynccontextmanager
async def open_session() -> AsyncIterator[ClientSession]:
    """Open, initialize, and close one authenticated MCP session."""
    url = os.environ.get("ITDK_MCP_URL", DEFAULT_MCP_URL)
    headers = {"Authorization": f"Bearer {required_master_key()}"}
    async with (
        AsyncClient(headers=headers) as client,
        streamable_http_client(url, http_client=client) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        yield session


async def call_tool(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
    *,
    preview: int = 0,
) -> dict[str, Any]:
    """Call one tool, print its result, and optionally preview its CSV."""
    print(f"\nCalling {name} with {json.dumps(arguments, sort_keys=True)}")
    result = await session.call_tool(name, arguments)
    dumped = result.model_dump(mode="json", by_alias=True)
    if dumped.get("isError"):
        print(json.dumps(dumped, indent=2, sort_keys=True))
        return dumped

    metadata = dumped.get("structuredContent")
    if not isinstance(metadata, dict):
        raise RuntimeError("Server returned no structuredContent metadata.")
    print(json.dumps(metadata, indent=2, sort_keys=True))
    if preview > 0:
        preview_csv(metadata, preview)
    return metadata


def local_output_path(server_file_path: str) -> Path | None:
    """Map a server output path to the client's mounted output directory."""
    server_path = Path(server_file_path)
    if server_path.is_file():
        return server_path

    local_root_value = os.environ.get("ITDK_OUTPUT_DIR")
    if not local_root_value:
        return None
    server_root = Path(os.environ.get("ITDK_SERVER_OUTPUT_DIR", DEFAULT_SERVER_OUTPUT_DIR))
    try:
        relative_path = server_path.relative_to(server_root)
    except ValueError as exc:
        raise RuntimeError(f"Returned path is outside expected server output root {server_root}.") from exc
    return Path(local_root_value) / relative_path


def preview_csv(metadata: dict[str, Any], row_limit: int) -> None:
    """Print the header and up to row_limit data rows from a returned CSV."""
    path_value = metadata.get("file_path")
    if not isinstance(path_value, str):
        raise RuntimeError("Metadata file_path is missing or is not a string.")
    local_path = local_output_path(path_value)
    if local_path is None:
        print("CSV preview skipped: set ITDK_OUTPUT_DIR to the visible output mount.")
        return
    if not local_path.is_file():
        print(f"CSV preview skipped: mapped file does not exist at {local_path}.")
        return

    print(f"\nCSV preview from {local_path}:")
    with local_path.open(encoding="utf-8", newline="") as source:
        reader = csv.reader(source)
        for index, row in enumerate(reader):
            print(json.dumps(row, ensure_ascii=False))
            if index >= row_limit:
                break


def preview_argument(parser: Any, *, default: int = 5) -> None:
    parser.add_argument(
        "--preview",
        type=int,
        default=default,
        metavar="ROWS",
        help="preview this many CSV data rows when the output volume is visible",
    )
