"""File/path handlers: path.get, files.list."""
from __future__ import annotations

import os

from agent_core import workdir

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ..dispatcher import handler, HandlerContext


@handler(ReqMethod.PATH_GET)
async def path_get(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload={"path": str(workdir())})


@handler(ReqMethod.FILES_LIST)
async def files_list(req, ctx: HandlerContext):
    path = req.params.get("path") or str(workdir())
    try:
        entries = sorted(os.listdir(path))
    except OSError as e:
        return AgentResponse(req.request_id, ok=False, error=str(e))
    return AgentResponse(req.request_id, payload={"files": entries, "path": path})
