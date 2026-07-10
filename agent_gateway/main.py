"""FastAPI gateway for the myAgent core — jiuwenswarm-style architecture.

Boots:
  - SessionManager (one agent_core Session per chat session; unchanged)
  - MessageHandler (double-queue inbound routing)
  - ChannelManager + WebChannel (method-routed WS at /ws)
  - HeartbeatService (gateway liveness)
  - REST routes (session list/create/delete, SSE, health, agents, models, skills)
    kept for the existing frontend and SSE clients during the migration.

agent_core is untouched; everything new lives in agent_gateway.common,
channel_manager, message_handler, routing, heartbeat, gateway_push.

Run from the myAgent directory:
    uvicorn agent_gateway.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from agent_gateway.debug import debug, is_enabled as _debug_enabled

import code
from agent_gateway.sessions import manager, GatewaySession
from agent_gateway.schemas import (
    CreateSession, UserMessage, PermissionResponse,
    AgentCreate, AgentUpdate, ModelConfig,
)
from agent_core import model_config
from agent_gateway import sse, db, pipe as pipe_mod

# New architecture
from agent_gateway.channel_manager import ChannelManager
from agent_gateway.channel_manager.web import WebChannel, WebChannelConfig, register_web_handlers
from agent_gateway.message_handler import MessageHandler
from agent_gateway.heartbeat import HeartbeatService


# ── jiuwenswarm-style wiring (module level so /ws is registered at import) ──
# The WebChannel only needs the module-level `manager` to mount its route;
# services that need async start (MessageHandler loop, Heartbeat) are started
# in the lifespan below.
_message_handler = MessageHandler(manager)
_channel_manager = ChannelManager()
_channel_manager.set_message_handler(_message_handler)

_web_channel = WebChannel(WebChannelConfig(
    enabled=True,
    host=os.environ.get("GATEWAY_HOST", "0.0.0.0"),
    port=int(os.environ.get("GATEWAY_PORT", "8000")),
    path="/ws",
), sessions=manager, message_handler=_message_handler)
register_web_handlers(_web_channel, manager)
_channel_manager.register(_web_channel)

_heartbeat = HeartbeatService(interval=30.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    debug("gateway starting AGENT_DEBUG=%s", _debug_enabled())
    db.init_pool(os.environ.get("DATABASE_URL"))
    pipe_mod.init_redis(os.environ.get("REDIS_URL"))
    await _message_handler.start()
    await _channel_manager.start_all()
    await _heartbeat.start()
    try:
        yield
    finally:
        await _heartbeat.stop()
        await _channel_manager.stop_all()
        await _message_handler.stop()
        await pipe_mod.close_redis()
        db.close_pool()


app = FastAPI(title="myAgent gateway", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten per-deploy
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the WS route now that `app` exists (route registration at import time).
_web_channel.mount(app)

IDLE_TIMEOUT = 30 * 60           # 30 min idle session eviction (RAM only; DB row kept)
_last_cleanup = time.time()


async def _need_session(sid: str) -> GatewaySession:
    """Resolve a session, hydrating from the DB if it's persisted but not live."""
    loop = asyncio.get_running_loop()
    gs = await asyncio.to_thread(manager.get_or_hydrate, sid, loop)
    if gs is None:
        raise HTTPException(status_code=404, detail="session not found")
    return gs


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


# ── session lifecycle (REST, kept for existing frontend / SSE clients) ──

@app.post("/api/sessions")
async def create_session(body: CreateSession = CreateSession()):
    _maybe_cleanup()
    loop = asyncio.get_running_loop()
    gs = manager.create(body.transport, loop=loop)
    return {"session_id": gs.session_id, "transport": gs.transport}


@app.get("/api/sessions/{sid}/status")
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


@app.get("/api/sessions")
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


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    gs = manager.get(sid)
    if gs is not None:
        gs.interrupt()
        manager.drop(sid)
    await asyncio.to_thread(db.delete_session_row, sid)
    return {"ok": True}


@app.websocket("/api/sessions/{sid}")
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

@app.post("/api/sessions/{sid}/messages")
async def post_message(sid: str, body: UserMessage):
    gs = await _need_session(sid)
    ok = gs.post_message(body.text)
    if not ok:
        raise HTTPException(status_code=409, detail="a turn is already in flight")
    return {"ok": True}


@app.post("/api/sessions/{sid}/permissions/{rid}/respond")
async def respond_permission(sid: str, rid: str, body: PermissionResponse):
    gs = await _need_session(sid)
    ok = gs.grant(rid, body.allow, body.modify)
    if not ok:
        raise HTTPException(status_code=404, detail="no pending permission with that id")
    return {"ok": True}


@app.post("/api/sessions/{sid}/interrupt")
async def interrupt(sid: str):
    gs = await _need_session(sid)
    gs.interrupt()
    return {"ok": True}


# ── read-only dot-dir views ──

@app.get("/api/health")
async def health():
    """Liveness + backend readiness. Reports DB/Redis as 'in_memory' when the
    optional env vars are unset (graceful degradation, not a failure)."""
    db_ok = db._pool is not None
    redis_ok = pipe_mod.redis_enabled()
    return {
        "status": "ok",
        "db": "postgres" if db_ok else "in_memory",
        "redis": "redis" if redis_ok else "in_memory",
        "model": os.environ.get("MODEL_ID", "?"),
        "sessions_live": len(manager.all()),
    }


@app.get("/api/skills")
async def get_skills():
    return code.scan_skills()


# ── Agent definitions (.agents/<name>.json) ──
@app.get("/api/agents")
async def list_agents():
    return code.list_agents()


@app.post("/api/agents")
async def create_agent(body: AgentCreate):
    try:
        return code.save_agent(body.name, body.description, body.prompt,
                               body.model, body.tools)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/agents/{name}")
async def update_agent(name: str, body: AgentUpdate):
    try:
        return code.save_agent(name, body.description, body.prompt,
                               body.model, body.tools)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/agents/{name}")
async def delete_agent(name: str):
    try:
        ok = code.delete_agent(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="agent not found")
    return {"ok": True}


# ── Global model config (.agents/model.json) ──
@app.get("/api/models")
async def get_models():
    """Current model config with api_key masked. The raw key never leaves this."""
    return model_config.get_config_masked()


@app.put("/api/models")
async def update_models(body: ModelConfig):
    """Persist model config. Empty api_key preserves the existing on-disk key.
    Takes effect next turn (loop re-reads model_config.model() each round)."""
    model_config.write_config(body.model_id, body.base_url,
                              body.api_key, body.fallback_model)
    return {"ok": True}


@app.get("/api/mcp")
async def get_mcp(sid: Optional[str] = None):
    """Connected MCP servers + their tools for a session (per-session
    Session.mcp_clients). Returns [] when no session or none connected."""
    gs = manager.get(sid) if sid else None
    if gs is None:
        return []
    clients = getattr(gs.agent, "mcp_clients", {}) or {}
    return [
        {
            "name": c.name,
            "tools": [
                {"name": t.get("name", ""), "description": t.get("description", "")}
                for t in (c.tools or [])
            ],
        }
        for c in clients.values()
    ]


# SSE routes registered by sse.py
sse.register(app, manager)


# ── file-api (REST file browser for the Sessions/Agent panels) ──
# The jiuwenswarm frontend browses on-disk session artifacts via /file-api/*
# (list-files, file-content). myAgent keeps conversation history in postgres,
# not on disk, so per-session file dirs are usually empty — but the routes
# must exist and return {files: []} for missing dirs instead of 404, else the
# SessionsPanel shows "Failed to load session files". Rooted at REPO_ROOT with
# path-traversal confinement; non-existent paths degrade to empty.
import pathlib as _pathlib

_FILE_API_ROOT = code.REPO_ROOT


def _resolve_under_root(rel: str) -> Optional[_pathlib.Path]:
    """Resolve a frontend-relative path under the file-api root, confining
    traversal. Returns None if the resolved path escapes the root.

    The AgentPanel browses the real mounted workspace (/app/workspace, from
    ~/.myAgent/workspace) under the frontend prefix `agent/workspace/...`.
    Rewrite that prefix to the real workspace so file previews resolve there."""
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


@app.get("/file-api/list-files")
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


@app.get("/file-api/file-content")
async def file_api_file_content(path: str = "", encoding: str = "utf-8"):
    from fastapi import Response
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
        # preview never 500s. Mirrors jiuwenswarm's charset_normalizer behavior
        # without adding the dependency.
        try:
            raw = full.read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        data = _decode_auto(raw)
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return Response(content=data.encode("utf-8"), media_type="text/plain; charset=utf-8")


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


@app.post("/file-api/rebuild-agent-data")
async def file_api_rebuild_agent_data():
    """Generate agent/workspace/agent-data.json (Record<folder_key, FileInfo[]>)
    for the jiuwenswarm AgentPanel file browser by walking the REAL mounted
    workspace (/app/workspace ← ~/.myAgent/workspace). The frontend browses it
    under the `agent/workspace/...` prefix; _resolve_under_root rewrites that
    prefix to the real workspace, and file display paths keep the prefix so
    previews resolve back through the same rewrite."""
    try:
        await asyncio.to_thread(_rebuild_agent_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


def _rebuild_agent_data() -> None:
    import json as _json
    import shutil
    ws_root = (_FILE_API_ROOT / "workspace").resolve()
    ws_root.mkdir(parents=True, exist_ok=True)

    # ── per-workspace skills: seed from presets (/app/skills/*) if missing.
    # Each workspace owns its copy (copy-if-missing preserves user edits); new
    # presets are seeded on the next rebuild. ──
    skills_dst = ws_root / "skills"
    skills_dst.mkdir(parents=True, exist_ok=True)
    skills_src = _FILE_API_ROOT / "skills"
    if skills_src.is_dir():
        for d in sorted(skills_src.iterdir()):
            if not d.is_dir():
                continue
            target = skills_dst / d.name
            if not target.exists():
                shutil.copytree(d, target)

    # ── memory: .memory/ holds config json. Seed a default config once so the
    # branch is visible even before the user adds entries. ──
    mem_dir = ws_root / ".memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    mem_cfg = mem_dir / "config.json"
    if not mem_cfg.exists():
        mem_cfg.write_text(_json.dumps({
            "enabled": True,
            "types": ["user", "feedback", "project", "reference"],
            "consolidate_threshold": 10,
            "index_file": "MEMORY.md",
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── walk workspace → Record<folder_key, FileInfo[]> ──
    # Skip hidden dirs/files except .memory (memory lives there). Other dot-dirs
    # (.tasks/.worktrees/.transcripts/…) stay hidden to keep the tree clean.
    folder_data: dict[str, list[dict]] = {}
    for entry in sorted(ws_root.rglob("*")):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        parts = entry.relative_to(ws_root).parts
        if any(p.startswith(".") and p != ".memory" for p in parts):
            continue
        if entry.name == "agent-data.json":
            continue  # avoid self-reference
        rel = entry.relative_to(ws_root).as_posix()
        rel_parent = entry.parent.relative_to(ws_root).as_posix()
        # folder_key mirrors getFolderKeyByFilePath('agent/' + parent): the
        # parent path with the 'agent/' prefix stripped → 'workspace/<parent>'.
        folder_key = "workspace" if rel_parent == "." else f"workspace/{rel_parent}"
        display_path = f"agent/workspace/{rel}"
        folder_data.setdefault(folder_key, []).append({
            "name": entry.name,
            "path": display_path,
            "isMarkdown": entry.suffix.lower() in {".md", ".mdx"},
        })
    sorted_folder_data = {
        k: sorted(v, key=lambda item: item["path"])
        for k, v in sorted(folder_data.items(), key=lambda item: item[0])
    }
    (ws_root / "agent-data.json").write_text(
        _json.dumps(sorted_folder_data, ensure_ascii=False, indent=2),
        encoding="utf-8")


@app.get("/file-api/ws-debug-config")
async def file_api_ws_debug_config():
    return {"wsDisableCompress": False}
