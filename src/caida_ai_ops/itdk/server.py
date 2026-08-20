"""Authenticated Streamable HTTP deployment for the unified MCP server."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..itdk_stdio import configure_itdk_runtime
from ..server import mcp
from .auth import BearerAuthMiddleware
from .config import Settings
from .structured_logging import configure_logging

LOGGER = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> Starlette:
    """Build one authenticated v2 MCP HTTP application exposing all tools."""
    active_settings = settings or Settings.from_env()
    active_settings.prepare_output_dir()
    configure_logging(active_settings.log_level, secrets=active_settings.log_redaction_values)

    database_pool = configure_itdk_runtime(active_settings)
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        host=active_settings.host,
    )

    async def healthz(_: Any) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def readyz(_: Any) -> JSONResponse:
        try:
            await anyio.to_thread.run_sync(database_pool.check)
        except Exception:
            LOGGER.warning("database readiness check failed", extra={"event": "database_not_ready"})
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return JSONResponse({"status": "ready"})

    app.routes.insert(0, Route("/healthz", healthz))
    app.routes.insert(1, Route("/readyz", readyz))
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        try:
            database_pool.open()
            async with original_lifespan(app):
                LOGGER.info("service initialized", extra={"event": "service_initialized"})
                yield
        finally:
            await anyio.to_thread.run_sync(database_pool.close)
            LOGGER.info("service stopped", extra={"event": "service_stopped"})

    app.router.lifespan_context = lifespan
    app.add_middleware(
        BearerAuthMiddleware,
        master_key=active_settings.master_key,
        excluded_paths=frozenset({"/healthz", "/readyz"}),
    )
    app.state.settings = active_settings
    app.state.database_pool = database_pool
    app.state.mcp_server = mcp
    return app
