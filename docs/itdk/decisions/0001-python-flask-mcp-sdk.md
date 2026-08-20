# 0001 — Python, Flask, and the official Python MCP SDK

**Status:** Accepted  
**Date:** 2026-08-19

## Context

The service needs a small HTTP host, typed runtime configuration, an eventual
MCP transport, and a PostgreSQL/dataframe-oriented implementation path. The
approved plan selects Python and Flask and requires the official MCP SDK.

## Decision

Use Python 3.12 with a `src/` package layout, Flask 3 for operational HTTP, and
the official `mcp` Python package for the MCP boundary. Dependency compatibility ranges are declared in
`pyproject.toml`; a reproducible lockfile will be generated only after the
project chooses and documents its package-resolution workflow.

Keep Flask lifecycle/configuration concerns separate from later MCP tool and
data-access modules. The service defaults to loopback binding. Docker Compose
overrides the container bind address to its internal interface while publishing
the port on host loopback only.

## Consequences

- Phase 3 verified that SDK SSE is ASGI-native and adopted the composition in
  [decision 0003](0003-asgi-sdk-sse-composition.md). Uvicorn is now the process
  server; Flask is adapted beneath Starlette for operational routes.
- Operators must provide secrets at runtime; no image layer or committed file
  contains them.
