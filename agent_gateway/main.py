"""FastAPI gateway for the myAgent core.

Run from the myAgent directory (so code.py's WORKDIR = cwd picks up skills/
and the dot-dirs):
    uvicorn agent_gateway.main:app --host 0.0.0.0 --port 8000

Routes (spec §4):
  POST /api/sessions                       create session (transport=ws|sse|auto)
  WS   /api/sessions/{id}                  bidirectional event channel (?last_seq=N to resume)
  GET  /api/sessions/{id}/status           session state + active transports
  POST /api/sessions/{id}/messages         user message (REST fallback / SSE-mode input)
  POST /api/sessions/{id}/permissions/{rid}/respond   permission response (SSE mode)
  POST /api/sessions/{id}/interrupt        interrupt (SSE mode)
  GET  /api/skills  /api/mcp   read-only dot-dir views
SSE routes live in sse.py (spec §4.1).
"""
from __future__ import annotations
import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import code
from agent_gateway.sessions import manager, GatewaySession
from agent_gateway.schemas import (
    CreateSession, UserMessage, PermissionResponse,
    AgentCreate, AgentUpdate, ModelConfig,
)
from agent_core import model_config
from agent_gateway import sse, db, pipe as pipe_mod


@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB is the durable source of truth; the in-memory dict is a live cache.
    # If DATABASE_URL is unset, persistence degrades to no-op (in-memory only).
    db.init_pool(os.environ.get("DATABASE_URL"))
    # Redis is the hot event pipe; if REDIS_URL is unset, falls back to in-proc
    # queue+deque (InMemoryPipe).
    pipe_mod.init_redis(os.environ.get("REDIS_URL"))
    try:
        yield
    finally:
        await pipe_mod.close_redis()
        db.close_pool()


app = FastAPI(title="myAgent gateway", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten per-deploy
    allow_methods=["*"],
    allow_headers=["*"],
)

IDLE_TIMEOUT = 30 * 60           # 30 min idle session eviction (RAM only; DB row kept)
_last_cleanup = time.time()


async def _need_session(sid: str) -> GatewaySession:
    """Resolve a session, hydrating from the DB if it's persisted but not live."""
    loop = asyncio.get_running_loop()
    gs = await asyncio.to_thread(manager.get_or_hydrate, sid, loop)
    if gs is None:
        raise HTTPException(status_code=404, detail="session not found")
    return gs


def _maybe_cleanup():
    """Evict from RAM sessions idle > IDLE_TIMEOUT whose worker is not alive.
    The DB row is kept so the session can be re-hydrated on demand."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < 60:
        return
    _last_cleanup = now
    for gs in manager.all():
        if now - gs.last_activity > IDLE_TIMEOUT:
            if gs._worker is None or not gs._worker.is_alive():
                manager.drop(gs.session_id)


# ── session lifecycle ──

@app.post("/api/sessions")
async def create_session(body: CreateSession = CreateSession()):
    _maybe_cleanup()
    loop = asyncio.get_running_loop()
    gs = manager.create(body.transport, loop=loop)
    return {"session_id": gs.session_id, "transport": gs.transport}


@app.get("/api/sessions/{sid}/status")
async def session_status(sid: str):
    gs = await _need_session(sid)
    return {
        "session_id": gs.session_id,
        "transport": gs.transport,
        "active_sinks": [type(s).__name__ for s in gs.agent.sinks],
        "last_seq": gs.agent._seq,
        "buffered": gs.pipe.count(),
        "worker_alive": gs._worker is not None and gs._worker.is_alive(),
        "history_len": len(gs.agent.record),
    }

@app.get("/api/sessions")
async def list_sessions():
    """List all sessions (sidebar). DB is source of truth; live sessions overlay
    fresher last_activity/title between turns."""
    _maybe_cleanup()
    rows = await asyncio.to_thread(db.list_session_rows)
    by_sid: dict[str, dict] = {}
    for r in rows:
        by_sid[r["session_id"]] = {
            "session_id": r["session_id"],
            "transport": r["transport"],
            "created_at": r["created_at"],
            "last_activity": r["last_activity"],
            "title": r["title"],
            "history_len": len(r.get("chat_record") or []),
        }
    for gs in manager.all():
        by_sid[gs.session_id] = gs.meta()  # live is at least as fresh
    return sorted(by_sid.values(), key=lambda m: m["last_activity"], reverse=True)

@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    gs = manager.get(sid)
    if gs is not None:
        gs.interrupt()
        manager.drop(sid)
    await asyncio.to_thread(db.delete_session_row, sid)
    return {"ok": True}


@app.websocket("/api/sessions/{sid}")
async def ws_endpoint(ws: WebSocket, sid: str,
                      last_seq: int = Query(default=0, ge=0)):
    loop = asyncio.get_running_loop()
    gs = await asyncio.to_thread(manager.get_or_hydrate, sid, loop)
    if gs is None:
        await ws.accept()
        await ws.send_json({"seq": 0, "kind": "error", "payload": {"error": "session not found"}})
        await ws.close(code=4404)
        return
    try:
        from agent_gateway.ws import handle_ws
        await handle_ws(ws, gs, last_seq)
    except WebSocketDisconnect:
        pass


# ── REST input / control (SSE-mode + non-streaming fallback) ──

@app.post("/api/sessions/{sid}/messages")
async def post_message(sid: str, body: UserMessage):
    gs = await _need_session(sid)
    ok = gs.post_message(body.text)
    if not ok:
        raise HTTPException(status_code=409, detail="a turn is already in flight")
    return {"ok": True}


@app.post("/api/sessions/{sid}/permissions/{rid}/respond")
async def respond_permission(sid: str, rid: str, body: PermissionResponse):
    gs = await _need_session(sid)
    ok = gs.grant(rid, body.allow, body.modify)
    if not ok:
        raise HTTPException(status_code=404, detail="no pending permission with that id")
    return {"ok": True}


@app.post("/api/sessions/{sid}/interrupt")
async def interrupt(sid: str):
    gs = await _need_session(sid)
    gs.interrupt()
    return {"ok": True}


# ── read-only dot-dir views ──

@app.get("/api/health")
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


@app.get("/api/skills")
async def get_skills():
    return code.scan_skills()


# ── Agent definitions (.agents/<name>.json) ──
@app.get("/api/agents")
async def list_agents():
    return code.list_agents()


@app.post("/api/agents")
async def create_agent(body: AgentCreate):
    try:
        return code.save_agent(body.name, body.description, body.prompt,
                               body.model, body.tools)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/agents/{name}")
async def update_agent(name: str, body: AgentUpdate):
    try:
        return code.save_agent(name, body.description, body.prompt,
                               body.model, body.tools)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/agents/{name}")
async def delete_agent(name: str):
    try:
        ok = code.delete_agent(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="agent not found")
    return {"ok": True}


# ── Global model config (.agents/model.json) ──
@app.get("/api/models")
async def get_models():
    """Current model config with api_key masked. The raw key never leaves this."""
    return model_config.get_config_masked()


@app.put("/api/models")
async def update_models(body: ModelConfig):
    """Persist model config. Empty api_key preserves the existing on-disk key.
    Takes effect next turn (loop re-reads model_config.model() each round)."""
    model_config.write_config(body.model_id, body.base_url,
                              body.api_key, body.fallback_model)
    return {"ok": True}


@app.get("/api/mcp")
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


# SSE routes registered by sse.py
sse.register(app, manager)
