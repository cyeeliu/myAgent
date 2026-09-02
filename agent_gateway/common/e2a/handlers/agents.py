"""Agent definition handlers: agents.list / get / create / update / delete."""
from __future__ import annotations

from agent_core import list_agents, get_agent, save_agent, delete_agent

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ..dispatcher import handler, HandlerContext


@handler(ReqMethod.AGENTS_LIST)
async def agents_list(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload={"agents": list_agents()})


@handler(ReqMethod.AGENTS_GET)
async def agents_get(req, ctx: HandlerContext):
    name = req.params.get("name", "")
    ag = get_agent(name) if name else None
    return AgentResponse(req.request_id, payload={"agent": ag})


@handler(ReqMethod.AGENTS_CREATE)
async def agents_create(req, ctx: HandlerContext):
    try:
        ag = save_agent(req.params.get("name", ""), req.params.get("description", ""),
                             req.params.get("prompt", ""), req.params.get("model"),
                             req.params.get("tools") or [])
    except ValueError as e:
        return AgentResponse(req.request_id, ok=False, error=str(e))
    return AgentResponse(req.request_id, payload=ag)


@handler(ReqMethod.AGENTS_UPDATE)
async def agents_update(req, ctx: HandlerContext):
    try:
        ag = save_agent(req.params.get("name", ""), req.params.get("description", ""),
                             req.params.get("prompt", ""), req.params.get("model"),
                             req.params.get("tools") or [])
    except ValueError as e:
        return AgentResponse(req.request_id, ok=False, error=str(e))
    return AgentResponse(req.request_id, payload=ag)


@handler(ReqMethod.AGENTS_DELETE)
async def agents_delete(req, ctx: HandlerContext):
    try:
        ok_flag = delete_agent(req.params.get("name", ""))
    except ValueError as e:
        return AgentResponse(req.request_id, ok=False, error=str(e))
    if not ok_flag:
        return AgentResponse(req.request_id, ok=False, error="agent not found")
    return AgentResponse(req.request_id, payload={"ok": True})
