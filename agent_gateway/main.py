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
  GET  /api/skills  /api/tasks  /api/memories   read-only dot-dir views
SSE routes live in sse.py (spec §4.1).
"""
from __future__ import annotations
import asyncio
import time
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import code
from agent_gateway.sessions import manager, GatewaySession
from agent_gateway.schemas import CreateSession, UserMessage, PermissionResponse
from agent_gateway import sse

app = FastAPI(title="myAgent gateway")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten per-deploy
    allow_methods=["*"],
    allow_headers=["*"],
)

IDLE_TIMEOUT = 30 * 60           # 30 min idle session cleanup
_last_cleanup = time.time()


def _need_session(sid: str) -> GatewaySession:
    gs = manager.get(sid)
    if gs is None:
        raise HTTPException(status_code=404, detail="session not found")
    return gs


def _maybe_cleanup():
    """Drop sessions idle > IDLE_TIMEOUT whose worker is not alive."""
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
    gs = _need_session(sid)
    return {
        "session_id": gs.session_id,
        "transport": gs.transport,
        "active_sinks": [type(s).__name__ for s in gs.agent.sinks],
        "last_seq": gs.agent._seq,
        "buffered": len(gs.buffer),
        "worker_alive": gs._worker is not None and gs._worker.is_alive(),
        "history_len": len(gs.agent.history),
    }


@app.websocket("/api/sessions/{sid}")
async def ws_endpoint(ws: WebSocket, sid: str,
                      last_seq: int = Query(default=0, ge=0)):
    gs = manager.get(sid)
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
    gs = _need_session(sid)
    ok = gs.post_message(body.text)
    if not ok:
        raise HTTPException(status_code=409, detail="a turn is already in flight")
    return {"ok": True}


@app.post("/api/sessions/{sid}/permissions/{rid}/respond")
async def respond_permission(sid: str, rid: str, body: PermissionResponse):
    gs = _need_session(sid)
    ok = gs.grant(rid, body.allow, body.modify)
    if not ok:
        raise HTTPException(status_code=404, detail="no pending permission with that id")
    return {"ok": True}


@app.post("/api/sessions/{sid}/interrupt")
async def interrupt(sid: str):
    gs = _need_session(sid)
    gs.interrupt()
    return {"ok": True}


# ── read-only dot-dir views ──

@app.get("/api/skills")
async def get_skills():
    return code.scan_skills()


@app.get("/api/tasks")
async def get_tasks():
    return [code.get_task_json(t.id) for t in code.list_tasks()]


@app.get("/api/memories")
async def get_memories():
    idx = code.MEMORY_INDEX
    if not idx.exists():
        return {"text": ""}
    return {"text": idx.read_text()}


# SSE routes registered by sse.py
sse.register(app, manager)
