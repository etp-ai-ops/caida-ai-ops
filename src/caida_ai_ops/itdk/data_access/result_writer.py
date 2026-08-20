"""Streaming, deterministic CSV output for database cursors."""

from __future__ import annotations

import csv
import math
import os
import re
import secrets
import tempfile
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

ColumnType = Literal["string", "int32", "int64", "float64", "inet"]
NULL_SENTINEL: Final = r"\N"
_SAFE_PREFIX = re.compile(r"[^a-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: ColumnType


@dataclass(frozen=True, slots=True)
class CsvResult:
    file_path: Path
    row_count: int
    columns: tuple[Column, ...]


class CsvResultWriter:
    """Write rows incrementally and publish a complete file with one rename."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    def write(
        self,
        *,
        filename_prefix: str,
        columns: Sequence[Column],
        rows: Iterable[Sequence[object]],
    ) -> CsvResult:
        safe_prefix = _safe_filename_prefix(filename_prefix)
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
        final_path = self._output_dir / f"{safe_prefix}_{timestamp}_{secrets.token_hex(8)}.csv"
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._output_dir,
            prefix=f".{safe_prefix}_",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        row_count = 0

        try:
            # Shared-volume consumers join the container's fixed GID 10001.
            # Override mkstemp's private default while keeping access from
            # unrelated users disabled.
            os.fchmod(descriptor, 0o640)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
                writer = csv.writer(output, lineterminator="\n")
                writer.writerow(column.name for column in columns)
                for row in rows:
                    if len(row) != len(columns):
                        raise ValueError("query row does not match its fixed projection")
                    writer.writerow(
                        _serialize(value, column.type) for column, value in zip(columns, row, strict=True)
                    )
                    row_count += 1
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, final_path)
        except BaseException:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
            raise

        return CsvResult(final_path, row_count, tuple(columns))


def _safe_filename_prefix(value: str) -> str:
    normalized = _SAFE_PREFIX.sub("_", value.lower()).strip("_-")[:64]
    return normalized or "query"


def _serialize(value: object, column_type: ColumnType) -> str:
    if value is None:
        return NULL_SENTINEL
    if column_type in {"string", "inet"}:
        return str(value).replace("\\", "\\\\")
    if column_type in {"int32", "int64"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"expected integer value for {column_type}")
        return str(value)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("expected numeric value for float64")
    number = float(value)
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "Infinity" if number > 0 else "-Infinity"
    return repr(number)
