"""Session lifecycle handlers: list, create, switch, delete, status, rename."""
from __future__ import annotations

import asyncio

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ....debug import debug
from ..dispatcher import handler, HandlerContext
from ..helpers import list_sessions, session_status, now


@handler(ReqMethod.SESSION_LIST)
async def session_list(req, ctx: HandlerContext):
    rows = await asyncio.to_thread(list_sessions, ctx.sessions)
    return AgentResponse(req.request_id, payload={"sessions": rows})


@handler(ReqMethod.SESSION_CREATE)
async def session_create(req, ctx: HandlerContext):
    transport = req.params.get("transport", "auto")
    gs = ctx.sessions.create(transport, loop=ctx.loop,
                             sid=req.params.get("session_id") or None)
    return AgentResponse(req.request_id,
                         payload={"session_id": gs.session_id, "transport": gs.transport})


@handler(ReqMethod.SESSION_SWITCH)
async def session_switch(req, ctx: HandlerContext):
    sid = req.session_id
    if not sid:
        return AgentResponse(req.request_id, ok=False, error="missing session_id")
    gs = await asyncio.to_thread(ctx.sessions.get_or_hydrate, sid, ctx.loop)
    if gs is None:
        return AgentResponse(req.request_id, ok=False, error="session not found")
    return AgentResponse(req.request_id, payload=gs.meta())


@handler(ReqMethod.SESSION_DELETE)
async def session_delete(req, ctx: HandlerContext):
    sessions = ctx.sessions
    sid = req.session_id
    gs = sessions.get(sid) if sid else None
    if gs is not None:
        gs.interrupt()
        sessions.drop(gs.session_id)
    from agent_gateway import db
    from agent_gateway.sessions import cleanup_session_artifacts
    await asyncio.to_thread(db.delete_session_row, sid)
    await asyncio.to_thread(cleanup_session_artifacts, sid)
    return AgentResponse(req.request_id, payload={"ok": True})


@handler(ReqMethod.SESSION_STATUS)
async def session_status_handler(req, ctx: HandlerContext):
    sid = req.session_id
    if not sid:
        return AgentResponse(req.request_id, ok=False, error="missing session_id")
    gs = ctx.sessions.get(sid)
    if gs is None:
        return AgentResponse(req.request_id, ok=False, error="session not found")
    return AgentResponse(req.request_id, payload=session_status(gs))


@handler(ReqMethod.SESSION_RENAME)
async def session_rename(req, ctx: HandlerContext):
    sid = req.session_id
    if not sid:
        return AgentResponse(req.request_id, ok=False, error="missing session_id")
    title = str(req.params.get("title") or "")[:120]
    from agent_gateway import db
    gs = ctx.sessions.get(sid)
    record = gs.agent.record if gs is not None else None
    if record is not None:
        await asyncio.to_thread(db.save_chat_record, sid, record, now(), title)
    return AgentResponse(req.request_id, payload={"ok": True, "title": title})
