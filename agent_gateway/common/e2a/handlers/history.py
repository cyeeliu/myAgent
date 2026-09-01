"""History handler: history.get — stream conversation as history.message events."""
from __future__ import annotations

import asyncio

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ....debug import debug
from ..dispatcher import handler, HandlerContext
from ..helpers import emit_history_stream


@handler(ReqMethod.HISTORY_GET)
async def history_get(req, ctx: HandlerContext):
    sid = req.session_id
    if not sid:
        return AgentResponse(req.request_id, ok=False, error="missing session_id")
    gs = await asyncio.to_thread(ctx.sessions.get_or_hydrate, sid, ctx.loop)
    if gs is None:
        return AgentResponse(req.request_id, payload={
            "accepted": True, "session_id": sid, "empty": True,
            "page_idx": (req.params.get("page_idx") if isinstance(req.params, dict) else None) or 1,
        })
    page_idx = (req.params.get("page_idx") if isinstance(req.params, dict) else None) or 1
    debug("history.get sid=%r seq_now=%r record_len=%d",
          sid, getattr(gs.agent, "_seq", 0), len(gs.agent.record or []))
    await asyncio.to_thread(emit_history_stream, gs, page_idx)
    return AgentResponse(req.request_id, payload={
        "accepted": True, "session_id": sid, "page_idx": page_idx,
    })
