"""Miscellaneous read-only routes: health, version, skills, MCP servers."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter

from agent_core import scan_skills
from agent_gateway import db, pipe as pipe_mod
from agent_gateway.sessions import manager
from agent_gateway.middleware import API_VERSION

router = APIRouter(tags=["misc"])


@router.get("/api/health")
async def health():
    """Liveness + backend readiness. Reports DB/Redis as 'in_memory' when the
    optional env vars are unset (graceful degradation, not a failure)."""
    db_ok = db._pool is not None
    redis_ok = pipe_mod.redis_enabled()
    return {
        "status": "ok",
        "db": "postgres" if db_ok else "in_memory",
        "redis": "redis" if redis_ok else "in_memory",
        "model": os.environ.get("MODEL_ID", "?"),
        "sessions_live": len(manager.all()),
    }


@router.get("/api/version")
async def version():
    """API version info for client capability negotiation."""
    return {
        "version": API_VERSION,
        "versioned_prefix": f"/api/{API_VERSION}",
        "endpoints": {
            "health": "/api/health",
            "sessions": "/api/sessions",
            "agents": "/api/agents",
            "models": "/api/models",
            "skills": "/api/skills",
            "file_api": "/file-api",
            "websocket": "/ws",
        },
    }


@router.get("/api/skills")
async def get_skills():
    return scan_skills()


@router.get("/api/mcp")
async def get_mcp(sid: Optional[str] = None):
    """Connected MCP servers + their tools for a session (per-session
    Session.mcp_clients). Returns [] when no session or none connected."""
    gs = manager.get(sid) if sid else None
    if gs is None:
        return []
    clients = getattr(gs.agent, "mcp_clients", {}) or {}
    return [
        {
            "name": c.name,
            "tools": [
                {"name": t.get("name", ""), "description": t.get("description", "")}
                for t in (c.tools or [])
            ],
        }
        for c in clients.values()
    ]
