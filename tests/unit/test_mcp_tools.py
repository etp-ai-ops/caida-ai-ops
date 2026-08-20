from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp import types

from caida_ai_ops.itdk.data_access import Column, CsvResult
from caida_ai_ops.itdk.mcp_tools import execute_tool_call, serialize_csv_result


class StubNodeRepository:
    def __init__(self, result: CsvResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[Any, ...]] = []

    def find_nodes_by_asn(self, asn: int) -> CsvResult:
        self.calls.append((asn,))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def repositories(result: CsvResult | Exception) -> tuple[Any, StubNodeRepository]:
    nodes = StubNodeRepository(result)
    return (
        SimpleNamespace(
            nodes=nodes,
            links=SimpleNamespace(),
            transit=SimpleNamespace(),
            hostnames=SimpleNamespace(),
        ),
        nodes,
    )


def test_exact_metadata_serialization_and_order(tmp_path: Path) -> None:
    result = CsvResult(
        tmp_path / "result.csv",
        7,
        (Column("node_id", "string"), Column("asn", "int64")),
    )
    assert serialize_csv_result(result) == {
        "file_path": str(tmp_path / "result.csv"),
        "row_count": 7,
        "columns": [
            {"name": "node_id", "type": "string"},
            {"name": "asn", "type": "int64"},
        ],
    }


async def test_valid_call_dispatches_and_returns_only_metadata(tmp_path: Path) -> None:
    expected = CsvResult(tmp_path / "result.csv", 1, (Column("asn", "int64"),))
    repos, nodes = repositories(expected)
    response = await execute_tool_call(repos, "find_nodes_by_asn", {"asn": 64500})
    assert response == serialize_csv_result(expected)
    assert nodes.calls == [(64500,)]


async def test_invalid_input_returns_stable_safe_error_without_dispatch(tmp_path: Path, caplog: Any) -> None:
    repos, nodes = repositories(CsvResult(tmp_path / "unused", 0, ()))
    with caplog.at_level(logging.WARNING, logger="caida_ai_ops.itdk.mcp_tools"):
        response = await execute_tool_call(repos, "find_nodes_by_asn", {"asn": 0})
    assert isinstance(response, types.CallToolResult)
    assert response.is_error is True
    assert [item.text for item in response.content if isinstance(item, types.TextContent)] == [
        "INVALID_ARGUMENT"
    ]
    assert nodes.calls == []
    assert '"asn": 0' not in caplog.text


async def test_internal_failure_returns_stable_error_and_sanitized_log(caplog: Any) -> None:
    sensitive = "database-secret-and-query-text"
    repos, _ = repositories(RuntimeError(sensitive))
    with caplog.at_level(logging.ERROR, logger="caida_ai_ops.itdk.mcp_tools"):
        response = await execute_tool_call(repos, "find_nodes_by_asn", {"asn": 64500})
    assert isinstance(response, types.CallToolResult)
    assert response.is_error is True
    assert response.content[0].text == "INTERNAL_ERROR"
    assert sensitive not in caplog.text
    record = caplog.records[-1]
    assert record.context == {
        "code": "INTERNAL_ERROR",
        "tool": "find_nodes_by_asn",
        "category": "RuntimeError",
    }
