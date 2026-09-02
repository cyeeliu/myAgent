"""FastAPI APIRouter modules, grouped by domain.

Each module exports a ready-to-include ``APIRouter``. ``app.py`` includes them
all via ``include_routers(app)``.
"""
from __future__ import annotations

from fastapi import FastAPI

from . import sessions, agents, models, misc, file_api


def include_routers(app: FastAPI) -> None:
    """Mount all domain routers + the SSE stream route onto ``app``."""
    app.include_router(sessions.router)
    app.include_router(agents.router)
    app.include_router(models.router)
    app.include_router(misc.router)
    app.include_router(file_api.router)
    # SSE routes are registered via sse.register (legacy pattern).
    from agent_gateway import sse
    from agent_gateway.sessions import manager
    sse.register(app, manager)
