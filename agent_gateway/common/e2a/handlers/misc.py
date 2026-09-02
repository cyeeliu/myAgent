"""Misc handlers: memory.compute, tts.synthesize, commands, channel, heartbeat."""
from __future__ import annotations

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ..dispatcher import handler, HandlerContext
from ..helpers import memory_compute, now


@handler(ReqMethod.MEMORY_COMPUTE)
async def memory_compute_handler(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=memory_compute())


@handler(ReqMethod.TTS_SYNTHESIZE)
async def tts_synthesize(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload={"success": False, "reason": "no_tts"})


@handler(ReqMethod.COMMAND_COMPACT)
async def command_compact(req, ctx: HandlerContext):
    return await _command_handler(req, ctx)


@handler(ReqMethod.COMMAND_CONTEXT)
async def command_context(req, ctx: HandlerContext):
    return await _command_handler(req, ctx)


@handler(ReqMethod.COMMAND_MODEL)
async def command_model(req, ctx: HandlerContext):
    return await _command_handler(req, ctx)


async def _command_handler(req, ctx: HandlerContext):
    sid = req.session_id
    if not sid:
        return AgentResponse(req.request_id, ok=False, error="missing session_id")
    gs = ctx.sessions.get(sid)
    if gs is None:
        return AgentResponse(req.request_id, ok=False, error="session not found")
    text = f"/{req.method.value.split('.')[1]}"
    gs.post_message(text)
    return AgentResponse(req.request_id, payload={"ok": True})


@handler(ReqMethod.CHANNEL_GET)
async def channel_get(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload={"channel_id": req.channel_id})


@handler(ReqMethod.HEARTBEAT_PING)
async def heartbeat_ping(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload={"ok": True, "t": now()})
