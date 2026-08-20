from __future__ import annotations

import csv
import os
import stat
from pathlib import Path

import pytest

from caida_ai_ops.itdk.data_access.result_writer import Column, CsvResultWriter

COLUMNS = (
    Column("text", "string"),
    Column("address", "inet"),
    Column("small", "int32"),
    Column("large", "int64"),
    Column("number", "float64"),
)


def test_streaming_csv_exact_format_escaping_nulls_and_numbers(tmp_path: Path) -> None:
    writer = CsvResultWriter(tmp_path)
    rows = iter(
        [
            ('comma,quote"newline\nslash\\N', "2001:db8::1", 7, 9_223_372_036_854_775_807, 1.25),
            (None, None, -1, 0, float("nan")),
            (r"\N", "192.0.2.1", 2, 3, float("inf")),
            ("plain", "198.51.100.1", 4, 5, float("-inf")),
        ]
    )
    result = writer.write(filename_prefix="Unsafe Prefix/../", columns=COLUMNS, rows=rows)

    assert result.row_count == 4
    assert result.columns == COLUMNS
    assert result.file_path.parent == tmp_path
    assert result.file_path.name.startswith("unsafe_prefix_")
    assert result.file_path.suffix == ".csv"
    assert os.access(result.file_path, os.R_OK)
    assert stat.S_IMODE(result.file_path.stat().st_mode) == 0o640
    raw = result.file_path.read_bytes()
    assert b"\r\n" not in raw
    with result.file_path.open(encoding="utf-8", newline="") as source:
        parsed = list(csv.reader(source))
    assert parsed[0] == [column.name for column in COLUMNS]
    assert parsed[1] == [
        'comma,quote"newline\nslash\\\\N',
        "2001:db8::1",
        "7",
        "9223372036854775807",
        "1.25",
    ]
    assert parsed[2] == [r"\N", r"\N", "-1", "0", "NaN"]
    assert parsed[3] == [r"\\N", "192.0.2.1", "2", "3", "Infinity"]
    assert parsed[4][-1] == "-Infinity"


def test_rows_are_consumed_incrementally_without_a_length_or_second_iteration(
    tmp_path: Path,
) -> None:
    class OnePassRows:
        def __iter__(self) -> OnePassRows:
            if hasattr(self, "current"):
                raise AssertionError("rows iterated twice")
            self.current = 0
            return self

        def __next__(self) -> tuple[str]:
            if self.current == 1_205:
                raise StopIteration
            value = (f"N{self.current:04d}",)
            self.current += 1
            return value

    result = CsvResultWriter(tmp_path).write(
        filename_prefix="unlimited", columns=(Column("node_id", "string"),), rows=OnePassRows()
    )
    assert result.row_count == 1_205


@pytest.mark.parametrize(
    ("columns", "row", "error"),
    [
        ((Column("value", "int64"),), (True,), TypeError),
        ((Column("value", "int64"),), (1.5,), TypeError),
        ((Column("value", "float64"),), ("1.0",), TypeError),
        ((Column("a", "string"), Column("b", "string")), ("only one",), ValueError),
    ],
)
def test_serialization_failures_leave_no_file(
    tmp_path: Path,
    columns: tuple[Column, ...],
    row: tuple[object, ...],
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        CsvResultWriter(tmp_path).write(filename_prefix="failure", columns=columns, rows=[row])
    assert list(tmp_path.iterdir()) == []


def test_atomic_publish_failure_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr("caida_ai_ops.itdk.data_access.result_writer.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        CsvResultWriter(tmp_path).write(
            filename_prefix="failure", columns=(Column("value", "string"),), rows=[("x",)]
        )
    assert list(tmp_path.iterdir()) == []
