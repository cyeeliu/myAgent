"""File/path handlers: path.get, files.list."""
from __future__ import annotations

import os
from pathlib import Path

from agent_core import workdir

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod
from ..dispatcher import handler, HandlerContext


@handler(ReqMethod.PATH_GET)
async def path_get(req, ctx: HandlerContext):
    return AgentResponse(req.request_id, payload={"path": str(workdir())})


@handler(ReqMethod.FILES_LIST)
async def files_list(req, ctx: HandlerContext):
    requested_path = req.params.get("path") or str(workdir())
    # Security: constrain listing to the workspace root. Reject paths that
    # escape via .. or absolute paths outside the workspace.
    ws_root = Path(workdir()).resolve()
    try:
        target = Path(requested_path).resolve()
        target.relative_to(ws_root)
    except (ValueError, OSError):
        return AgentResponse(req.request_id, ok=False,
                             error="path outside workspace")
    try:
        entries = sorted(os.listdir(target))
    except OSError as e:
        return AgentResponse(req.request_id, ok=False, error=str(e))
    return AgentResponse(req.request_id, payload={"files": entries, "path": str(target)})
