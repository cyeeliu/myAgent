"""Session lifecycle routes: create / list / status / delete / WS / SSE /
messages / permissions / interrupt.

These REST + WS endpoints are kept for the existing frontend and SSE clients
during the migration to the method-routed ``/ws`` channel.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException

from agent_gateway.sessions import manager, cleanup_session_artifacts
from agent_gateway.schemas import CreateSession, UserMessage, PermissionResponse
from agent_gateway import db

router = APIRouter(tags=["sessions"])

IDLE_TIMEOUT = 30 * 60           # 30 min idle session eviction (RAM only; DB row kept)
_last_cleanup = time.time()


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


async def _need_session(sid: str):
    """Resolve a session, hydrating from the DB if it's persisted but not live."""
    loop = asyncio.get_running_loop()
    gs = await asyncio.to_thread(manager.get_or_hydrate, sid, loop)
    if gs is None:
        raise HTTPException(status_code=404, detail="session not found")
    return gs


@router.post("/api/sessions")
async def create_session(body: CreateSession = CreateSession()):
    _maybe_cleanup()
    loop = asyncio.get_running_loop()
    gs = manager.create(body.transport, loop=loop)
    return {"session_id": gs.session_id, "transport": gs.transport}


@router.get("/api/sessions/{sid}/status")
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


@router.get("/api/sessions")
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


@router.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    gs = manager.get(sid)
    if gs is not None:
        gs.interrupt()
        manager.drop(sid)
    await asyncio.to_thread(db.delete_session_row, sid)
    await asyncio.to_thread(cleanup_session_artifacts, sid)
    return {"ok": True}


@router.websocket("/api/sessions/{sid}")
async def ws_endpoint(ws: WebSocket, sid: str,
                      last_seq: int = Query(default=0, ge=0)):
    """Legacy event-frame WS (kept for the existing frontend during migration).
    New clients should use /ws (method-routed)."""
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

@router.post("/api/sessions/{sid}/messages")
async def post_message(sid: str, body: UserMessage):
    gs = await _need_session(sid)
    ok = gs.post_message(body.text)
    if not ok:
        raise HTTPException(status_code=409, detail="a turn is already in flight")
    return {"ok": True}


@router.post("/api/sessions/{sid}/permissions/{rid}/respond")
async def respond_permission(sid: str, rid: str, body: PermissionResponse):
    gs = await _need_session(sid)
    ok = gs.grant(rid, body.allow, body.modify)
    if not ok:
        raise HTTPException(status_code=404, detail="no pending permission with that id")
    return {"ok": True}


@router.post("/api/sessions/{sid}/interrupt")
async def interrupt(sid: str):
    gs = await _need_session(sid)
    gs.interrupt()
    return {"ok": True}
