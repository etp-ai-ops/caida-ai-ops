from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from psycopg import sql

from caida_ai_ops.itdk.data_access import Column, CsvResult
from caida_ai_ops.itdk.data_access.query import FixedQueryExecutor, Query


class Cursor:
    def __init__(self, name: str) -> None:
        self.name = name
        self.itersize = 0
        self.executed: tuple[Any, tuple[object, ...]] | None = None

    def __enter__(self) -> Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def execute(self, statement: Any, parameters: tuple[object, ...]) -> None:
        self.executed = (statement, parameters)


class Connection:
    def __init__(self) -> None:
        self.cursor_instance: Cursor | None = None

    def cursor(self, *, name: str) -> Cursor:
        self.cursor_instance = Cursor(name)
        return self.cursor_instance


class Pool:
    def __init__(self) -> None:
        self.connection_instance = Connection()

    @contextmanager
    def connection(self) -> Any:
        yield self.connection_instance


class Writer:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def write(self, **kwargs: Any) -> CsvResult:
        self.kwargs = kwargs
        return CsvResult(Path("/output/result.csv"), 0, tuple(kwargs["columns"]))


def test_executor_uses_named_streaming_cursor_and_bound_parameters() -> None:
    pool = Pool()
    writer = Writer()
    query = Query(
        sql.SQL("SELECT node_id FROM caida_itdk.itdk_node_as WHERE asn = %s"),
        (Column("node_id", "string"),),
        "find_nodes",
    )
    result = FixedQueryExecutor(pool, writer).execute(query, (64500,))
    cursor = pool.connection_instance.cursor_instance
    assert cursor is not None
    assert cursor.name.startswith("itdk_")
    assert len(cursor.name) == len("itdk_") + 32
    assert cursor.itersize == 1_000
    assert cursor.executed == (query.statement, (64500,))
    assert writer.kwargs == {
        "filename_prefix": "find_nodes",
        "columns": query.columns,
        "rows": cursor,
    }
    assert result.row_count == 0
