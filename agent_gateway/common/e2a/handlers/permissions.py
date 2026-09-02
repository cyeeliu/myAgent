"""Permissions handlers: permissions.tools.get / update / delete."""
from __future__ import annotations

from agent_core import permissions

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ..dispatcher import handler, HandlerContext


@handler(ReqMethod.PERMISSIONS_TOOLS_GET)
async def permissions_tools_get(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload=permissions.get_policy())


@handler(ReqMethod.PERMISSIONS_TOOLS_UPDATE)
async def permissions_tools_update(req, ctx: HandlerContext):
    tool = str(req.params.get("tool") or "").strip()
    level = str(req.params.get("level") or "").strip().lower()
    if not tool or level not in permissions.LEVELS:
        return AgentResponse(req.request_id, ok=False, error="invalid tool or level")
    return AgentResponse(req.request_id, payload=permissions.set_tool_level(tool, level))


@handler(ReqMethod.PERMISSIONS_TOOLS_DELETE)
async def permissions_tools_delete(req, ctx: HandlerContext):
    tool = str(req.params.get("tool") or "").strip()
    return AgentResponse(req.request_id, payload=permissions.delete_tool(tool))
