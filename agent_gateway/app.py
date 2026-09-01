"""FastAPI application factory.

``create_app()`` assembles the service container, creates the FastAPI app with
lifespan, mounts routes and middleware, and returns the ready-to-serve app.

Entry point: ``uvicorn agent_gateway.main:app`` where ``main.py`` is now a thin
shim that calls ``create_app()``.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_gateway.debug import debug, is_enabled as _debug_enabled
from agent_gateway.logging_config import setup_logging
from agent_gateway.middleware import setup_middleware, setup_exception_handlers
from agent_gateway.services.container import ServiceContainer
from agent_gateway.routes import include_routers


def create_app() -> FastAPI:
    """Create and configure the FastAPI gateway application."""
    # Unified logging setup (idempotent).
    setup_logging()

    # Service container — owns MessageHandler, ChannelManager, WebChannel, etc.
    container = ServiceContainer()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        debug("gateway starting AGENT_DEBUG=%s", _debug_enabled())
        await container.start()
        try:
            yield
        finally:
            await container.stop()

    app = FastAPI(title="myAgent gateway", lifespan=lifespan)

    # Middleware + exception handlers
    setup_middleware(app)
    setup_exception_handlers(app)

    # Mount the method-routed WS channel (/ws)
    container.mount_routes(app)

    # Include all domain routers (sessions, agents, models, misc, file-api, SSE)
    include_routers(app)

    return app
