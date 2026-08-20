"""matthewpp_core — shared infrastructure for the Matthew++ toolkit.

``ark_measurement.py`` (active Ark measurement: ping/traceroute/DNS) depends
on this module for the output-contract envelope (``Result`` / ``@_envelope``),
demo-mode detection, and output formatting (json/csv/md). This module depends
on nothing else in the toolkit.
"""

from __future__ import annotations

import contextvars
import csv
import io
import json
import logging
import math
import os
import socket
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

LOG = logging.getLogger("matthewpp")


class MatthewPPError(Exception):
    """Base class for every error the Matthew++ toolkit raises on purpose."""


@dataclass
class Result:
    """What a business-logic function returns internally.

    ``@_envelope`` unwraps this into the public response envelope. Plain
    (non-``Result``) return values are also accepted and treated as ``data``
    with no extra provenance — most simple functions just ``return data``.
    """

    data: Any
    warnings: list[str] = field(default_factory=list)
    provenance_extra: dict = field(default_factory=dict)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(function_name: str, persist: bool = False) -> Callable:
    """Decorator: turns a plain/`Result`-returning function into the shared
    output-contract envelope
    (``{"status", "function", "parameters", "data", "warnings",
    "provenance"}``), and turns any raised `MatthewPPError` (or unexpected
    exception) into a `status: "error"` response instead of an unwound
    stack trace.

    ``persist=True`` marks a function whose results are archived to the result
    store (see `persist_result`). Set it on measurements that cost packets, not
    on zero-packet discovery — the store is meant to be a record of probing,
    and burying it in `list_vps` calls would defeat that. Only the outermost
    persisting call in a nested chain writes a file.
    """

    def decorator(fn: Callable) -> Callable:
        import inspect

        sig = inspect.signature(fn)

        @wraps(fn)
        def wrapper(*args, **kwargs) -> dict:
            depth = _persist_depth.get()
            outermost = depth == 0
            token = _persist_depth.set(depth + 1)
            try:
                return (
                    _run(*args, **kwargs)
                    if not (persist and outermost)
                    else _run_and_persist(*args, **kwargs)
                )
            finally:
                _persist_depth.reset(token)

        def _run_and_persist(*args, **kwargs) -> dict:
            result = _run(*args, **kwargs)
            # Only successful measurements are archived. An error envelope
            # carries data: null, and every error path here (limit rejection,
            # unreachable mux, bad filter) fails *before* any packet leaves the
            # host — so there is neither a measurement to keep nor probing to
            # audit. Storing them would leave the store full of records that
            # describe nothing.
            if result.get("status") != "ok":
                return result
            path = persist_result(result, function_name)
            if path:
                result.setdefault("provenance", {})["result_path"] = path
            return result

        def _run(*args, **kwargs) -> dict:
            try:
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                parameters = {k: v for k, v in bound.arguments.items()}
            except TypeError:
                parameters = {"args": args, "kwargs": kwargs}

            try:
                raw = fn(*args, **kwargs)
            except MatthewPPError as exc:
                LOG.info("%s failed: %s: %s", function_name, type(exc).__name__, exc)
                return {
                    "status": "error",
                    "function": function_name,
                    "parameters": parameters,
                    "data": None,
                    "warnings": [],
                    "provenance": {"timestamp_utc": now_iso()},
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            except Exception as exc:  # noqa: BLE001 - last-resort, never a raw traceback out
                LOG.exception("%s raised an unexpected error", function_name)
                return {
                    "status": "error",
                    "function": function_name,
                    "parameters": parameters,
                    "data": None,
                    "warnings": [],
                    "provenance": {"timestamp_utc": now_iso()},
                    "error": {"type": "InternalError", "message": str(exc)},
                }

            if isinstance(raw, Result):
                data, warnings, provenance_extra = raw.data, raw.warnings, raw.provenance_extra
            else:
                data, warnings, provenance_extra = raw, [], {}

            provenance = {"timestamp_utc": now_iso(), **provenance_extra}
            return {
                "status": "ok",
                "function": function_name,
                "parameters": parameters,
                "data": data,
                "warnings": warnings,
                "provenance": provenance,
            }

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Result store — every measurement that costs packets is written to disk.
#
# Two reasons this exists, and they are different:
#   1. Results survive the conversation. Over MCP a response otherwise lives
#      only in the model's context window and is gone when the session ends.
#   2. It is an audit trail. Active probing sends packets from CAIDA-operated
#      hosts to third parties; there should be a durable record of what was
#      probed, from where, and by which call.
#
# Layout under $MATTHEWPP_RESULTS_DIR (default ~/.matthewpp/results):
#
#     index.jsonl                                    append-only, one line each
#     2026-08-19/20260819T211530Z_ping_a1b2c3.json   the full envelope
#
# Date directories keep any single directory small; the index makes listing
# cheap without opening every file.
# ---------------------------------------------------------------------------

RESULT_SCHEMA_VERSION = 1

# Only the outermost enveloped call is persisted. Several public functions are
# wrappers over others (check_dnssec_valid issues two dns_query calls,
# compare_ping_regions two pings), and writing a file per inner call would
# scatter one user-facing measurement across several records. A ContextVar,
# not a plain global, so concurrent tool calls in an async server cannot
# interleave their depth counts.
_persist_depth: contextvars.ContextVar[int] = contextvars.ContextVar("_persist_depth", default=0)


def results_dir() -> Path:
    """Directory results are written to. Override with MATTHEWPP_RESULTS_DIR."""
    default = (
        Path(os.environ["OUTPUT_DIR"]) / "ark-results"
        if os.environ.get("OUTPUT_DIR")
        else Path.home() / ".caida-ai-ops" / "results"
    )
    return Path(os.environ.get("MATTHEWPP_RESULTS_DIR", str(default))).expanduser()


def _toolkit_version() -> str:
    try:
        from importlib.metadata import version

        return version("caida-ai-ops")
    except Exception:  # noqa: BLE001 - running from a source checkout, not installed
        return "unknown"


def _extract_targets(parameters: dict) -> list[str]:
    """What this measurement was pointed at — the single most important thing
    to be able to search the audit trail by."""
    out: list[str] = []
    for key in ("target", "targets", "qname", "dst1", "dst2"):
        val = parameters.get(key)
        if isinstance(val, str):
            out.append(val)
        elif isinstance(val, (list, tuple)):
            out.extend(str(v) for v in val)
    return out


def _extract_vp_ids(data: Any) -> list[str]:
    """VP ids touched, best-effort across the several data shapes in use."""
    if isinstance(data, list):
        return [d["vp_id"] for d in data if isinstance(d, dict) and d.get("vp_id")]
    if isinstance(data, dict):
        for key in ("traces", "per_vp", "affected_vp_ids", "transiting_vp_ids"):
            val = data.get(key)
            if isinstance(val, list):
                if val and isinstance(val[0], dict):
                    return [d["vp_id"] for d in val if d.get("vp_id")]
                return [str(v) for v in val]
    return []


def persist_result(envelope_dict: dict, function_name: str) -> str | None:
    """Write one measurement envelope to the result store.

    Returns the path written, or None if persistence failed. A failed write is
    logged and swallowed: losing the archive copy of a measurement must never
    also lose the measurement itself, which is already in the caller's hands.
    """
    try:
        now = datetime.now(UTC)
        result_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
        slug = function_name.rsplit(".", 1)[-1]

        parameters = envelope_dict.get("parameters", {}) or {}
        data = envelope_dict.get("data")
        vp_ids = _extract_vp_ids(data)

        record = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "result_id": result_id,
            "saved_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "metadata": {
                "function": function_name,
                # Which backend produced this. Without it, synthetic results
                # are indistinguishable from real ones once on disk.
                "backend": "demo" if is_demo_mode() else "live",
                "toolkit_version": _toolkit_version(),
                "host": socket.gethostname(),
                "mux": os.environ.get("MATTHEWPP_MUX", "/run/ark/mux"),
                "targets": _extract_targets(parameters),
                "vp_count": len(vp_ids) or envelope_dict.get("provenance", {}).get("vp_count_used"),
                "vp_ids": vp_ids,
                "parameters": parameters,
            },
            "status": envelope_dict.get("status"),
            "warnings": envelope_dict.get("warnings", []),
            "provenance": envelope_dict.get("provenance", {}),
            "data": data,
        }
        if "error" in envelope_dict:
            record["error"] = envelope_dict["error"]

        base = results_dir()
        day_dir = base / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        rel = f"{now.strftime('%Y-%m-%d')}/{result_id}_{slug}.json"
        (base / rel).write_text(json.dumps(record, indent=2, default=str))

        index_line = {
            "result_id": result_id,
            "saved_at_utc": record["saved_at_utc"],
            "function": function_name,
            "backend": record["metadata"]["backend"],
            "status": record["status"],
            "targets": record["metadata"]["targets"],
            "vp_count": record["metadata"]["vp_count"],
            "path": rel,
        }
        with (base / "index.jsonl").open("a") as fh:
            fh.write(json.dumps(index_line, default=str) + "\n")
        return str(base / rel)
    except Exception:  # noqa: BLE001
        LOG.exception("could not persist result for %s", function_name)
        return None


def is_demo_mode() -> bool:
    return os.environ.get("MATTHEWPP_DEMO", "").lower() in {"1", "true", "yes"}


def demo_warning(system: str) -> list[str]:
    return [
        "DEMO MODE (MATTHEWPP_DEMO=1): response built from synthetic fixture data, "
        f"not live {system} results."
    ]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in kilometers
    (Earth radius assumed 6371.0088 km, the IUGG mean radius). Pure math, no
    backend needed."""
    r_km = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return round(2 * r_km * math.asin(math.sqrt(a)), 3)


# ---------------------------------------------------------------------------
# Output formatting (json / csv / md) — shared by every CLI in this toolkit.
# ---------------------------------------------------------------------------


def to_csv_string(rows: Iterable[dict]) -> str:
    """Render a list of flat dict rows as a CSV string."""
    rows = list(rows)
    if not rows:
        return ""
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def to_markdown_table(rows: Iterable[dict]) -> str:
    """Render a list of flat dict rows as a GitHub-flavored markdown table."""
    rows = list(rows)
    if not rows:
        return "(no rows)"
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    lines = ["| " + " | ".join(fieldnames) + " |", "|" + "|".join(["---"] * len(fieldnames)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(f, "")) for f in fieldnames) + " |")
    return "\n".join(lines)


def write_output(response: dict, path: str | None = None, output_format: str = "json") -> str | None:
    """Serialize a response envelope's ``data`` and either write it to a file
    or return it as a string.

    Args:
        response: A response envelope (the dict produced by `envelope`).
        path: If given, write to this file path and return the path. If
            ``None``, return the serialized string.
        output_format: ``"json"`` (the full envelope), ``"csv"``, or
            ``"md"`` (the latter two serialize ``response["data"]`` only,
            and expect it to be a list of dicts or a single dict).

    Returns:
        The file path written to, or the serialized string, per ``path``.

    Raises:
        ValueError: ``output_format`` is unknown, or csv/md was requested
            for a ``data`` shape that isn't a flat list of dicts.
    """
    if output_format == "json":
        rendered = json.dumps(response, indent=2, default=str)
    elif output_format in ("csv", "md"):
        rows = response.get("data")
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise ValueError(f"--format {output_format} requires list-shaped data, got {type(rows).__name__}")
        rendered = to_csv_string(rows) if output_format == "csv" else to_markdown_table(rows)
    else:
        raise ValueError(f"unknown output_format: {output_format!r}")

    if path is None:
        return rendered
    Path(path).write_text(rendered)
    return path
