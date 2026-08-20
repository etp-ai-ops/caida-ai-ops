"""Internal execution primitive for immutable SELECT specifications."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from psycopg import sql

from .pool import DatabasePool
from .result_writer import Column, CsvResult, CsvResultWriter


@dataclass(frozen=True, slots=True)
class Query:
    statement: sql.SQL
    columns: tuple[Column, ...]
    filename_prefix: str


class FixedQueryExecutor:
    """Execute package-owned query text and stream its cursor to the writer."""

    def __init__(self, pool: DatabasePool, writer: CsvResultWriter) -> None:
        self._pool = pool
        self._writer = writer

    def execute(self, query: Query, parameters: tuple[object, ...]) -> CsvResult:
        cursor_name = f"itdk_{uuid.uuid4().hex}"
        with self._pool.connection() as connection, connection.cursor(name=cursor_name) as cursor:
            cursor.itersize = 1_000
            cursor.execute(query.statement, parameters)
            return self._writer.write(
                filename_prefix=query.filename_prefix,
                columns=query.columns,
                rows=cursor,
            )
