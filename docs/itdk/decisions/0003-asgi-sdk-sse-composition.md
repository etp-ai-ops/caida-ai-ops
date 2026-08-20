# 0003 — ASGI composition for SDK SSE and Flask

**Status:** Accepted  
**Date:** 2026-08-19

## Context

The approved plan requires the official Python MCP SDK's standards-compliant
SSE connection/message transport and Flask operational endpoints. The SDK's
public `SseServerTransport` is ASGI-native; a WSGI server cannot truthfully host
its long-lived bidirectional session composition.

## Decision

Use the public low-level SDK `Server` and `SseServerTransport` APIs. Mount their
SSE connection and message applications beneath `/mcp` in Starlette, protect
the whole mount with service-owned strict bearer ASGI middleware, and mount the
existing Flask health application through Starlette's public `WSGIMiddleware`.
Run the composed application with Uvicorn instead of Gunicorn.

Stay on the maintained SDK v1 API line (`mcp>=1.28.1,<2`) for this phase. The
v2 API is a separate migration, while the plan explicitly requires the legacy
SSE connection/message boundary implemented by these verified v1 public APIs.

The SDK owns MCP parsing, lifecycle, discovery, schemas, calls, and SSE session
state. Service code supplies only public transport composition, authentication,
the seven fixed tool definitions/handlers, and repository dispatch. The outer
ASGI lifespan owns the database pool. Synchronous repository calls run in AnyIO
worker threads.

## Consequences

- `/mcp/sse` and `/mcp/messages/` implement the legacy SSE transport required
  by the plan; Streamable HTTP is not additionally exposed in this phase.
- Production requires an ASGI server. Flask remains intact for operational
  behavior but is no longer the top-level protocol host.
- In-memory SSE sessions and database pools are process-local. The container
  stays at one Uvicorn worker; scaling requires session-affine routing or a
  different transport/lifecycle design.
- The integration suite covers authentication on both SSE legs, advertised
  schemas, real repository calls, generated files, and SDK client
  interoperability. Unit lifespan coverage verifies graceful pool shutdown.
