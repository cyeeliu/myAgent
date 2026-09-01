"""File browser REST routes for the Sessions/Agent panels.

The myagent frontend browses on-disk session artifacts via ``/file-api/*``
(list-files, file-content). myAgent keeps conversation history in postgres,
not on disk, so per-session file dirs are usually empty — but the routes must
exist and return ``{files: []}`` for missing dirs instead of 404, else the
SessionsPanel shows "Failed to load session files".
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Response

from agent_core import REPO_ROOT
from agent_gateway.sessions import manager

router = APIRouter(prefix="/file-api", tags=["file-api"])

_FILE_API_ROOT = REPO_ROOT


def _resolve_under_root(rel: str) -> Optional[REPO_ROOT.__class__]:
    """Resolve a frontend-relative path under the file-api root, confining
    traversal. Returns None if the resolved path escapes the root.

    The AgentPanel browses the real mounted workspace (/app/workspace, from
    ~/.myAgent/workspace) under the frontend prefix `agent/workspace/...`.
    Rewrite that prefix to the real workspace so file previews resolve there."""
    import pathlib as _pathlib
    if not rel:
        return _FILE_API_ROOT
    _WS_PREFIX = "agent/workspace"
    if rel == _WS_PREFIX or rel.startswith(_WS_PREFIX + "/"):
        sub = "" if rel == _WS_PREFIX else rel[len(_WS_PREFIX) + 1:]
        ws_root = (_FILE_API_ROOT / "workspace").resolve()
        try:
            full = (ws_root / sub).resolve() if sub else ws_root
        except (OSError, ValueError):
            return None
        try:
            full.relative_to(ws_root)
        except ValueError:
            return None
        return full
    try:
        full = (_FILE_API_ROOT / rel).resolve()
    except (OSError, ValueError):
        return None
    try:
        full.relative_to(_FILE_API_ROOT.resolve())
    except ValueError:
        return None
    return full


def _decode_auto(raw: bytes) -> str:
    """Best-effort decode for encoding='auto'. Try utf-8 strict first, then a
    list of common legacy encodings, finally utf-8 with replacement so the
    preview always renders something instead of erroring."""
    for enc in ("utf-8", "gbk", "gb2312", "big5", "shift_jis", "euc_kr", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


@router.get("/list-files")
async def file_api_list_files(dir: str = ""):
    # Browsing a session's artifact dir: hydrate that session from the DB so
    # transcript.md/history.json are refreshed from the current chat record
    # (the DB is source of truth). `dir` looks like `agent/sessions/{sid}`.
    if dir.startswith("agent/sessions/"):
        sid = dir[len("agent/sessions/"):].strip("/")
        if sid:
            loop = asyncio.get_running_loop()
            gs = await asyncio.to_thread(manager.get_or_hydrate, sid, loop)
            # get_or_hydrate returns a cached session without re-writing files,
            # so refresh them explicitly from the live record every browse.
            if gs is not None:
                from agent_gateway.sessions import _write_session_files
                await asyncio.to_thread(_write_session_files, sid, gs.agent.record)
    full = _resolve_under_root(dir)
    if full is None:
        raise HTTPException(status_code=403, detail="forbidden_dir")
    if not full.exists() or not full.is_dir():
        return {"files": []}
    files = []
    try:
        entries = sorted(full.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return {"files": []}
    for entry in entries:
        try:
            rel = entry.relative_to(_FILE_API_ROOT)
        except ValueError:
            continue
        files.append({
            "name": entry.name,
            "path": str(rel),
            "isMarkdown": entry.suffix.lower() == ".md" if entry.is_file() else False,
            "isDirectory": entry.is_dir(),
        })
    return {"files": files}


@router.get("/file-content")
async def file_api_file_content(path: str = "", encoding: str = "utf-8"):
    full = _resolve_under_root(path)
    if full is None:
        raise HTTPException(status_code=403, detail="forbidden_path")
    if not full.exists() or not full.is_file():
        raise HTTPException(status_code=404, detail="file_not_found")
    try:
        data = full.read_text(encoding=encoding)
    except LookupError:
        # "auto" (or any unknown codec the frontend FileViewer sends): sniff the
        # encoding from the bytes, falling back to a tolerant utf-8 decode so the
        # preview never 500s.
        try:
            raw = full.read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        data = _decode_auto(raw)
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return Response(content=data.encode("utf-8"), media_type="text/plain; charset=utf-8")


@router.post("/rebuild-agent-data")
async def file_api_rebuild_agent_data():
    """Generate agent/workspace/agent-data.json for the myagent AgentPanel file
    browser by walking the REAL mounted workspace."""
    from agent_gateway.services.agent_data import rebuild_agent_data
    try:
        await asyncio.to_thread(rebuild_agent_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


@router.get("/ws-debug-config")
async def file_api_ws_debug_config():
    return {"wsDisableCompress": False}
