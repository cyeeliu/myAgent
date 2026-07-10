"""agent_compat — AgentRequest → in-process agent_core call (single-process).

In jiuwenswarm this would be a WS hop to a separate AgentServer. myAgent keeps
agent_core in-process, so "compat" is a direct dispatch: chat.send posts a user
message to the GatewaySession worker thread; session/history/config/skills/agents
methods call the matching agent_core / db / model_config function.

This module is the ONLY place that imports `code` (agent_core) for request
execution, keeping the agent_core boundary clean.
"""
from __future__ import annotations
import asyncio
from typing import Any, Optional

import code
from agent_core import model_config

from ..schema.agent import AgentRequest, AgentResponse
from ..schema.message import ReqMethod, Mode
from ...debug import debug


def _mode_str(req: AgentRequest) -> Optional[str]:
    return req.mode.value if req.mode else None


async def execute_agent_request(req: AgentRequest, *, sessions) -> AgentResponse:
    """Dispatch a normalized AgentRequest against agent_core in-process.

    `sessions` is the SessionManager (agent_gateway.sessions.manager). Methods
    that touch a live session resolve it via sessions.get_or_hydrate; chat.send
    posts to the worker thread and returns immediately (events stream back over
    the pipe). Returns an AgentResponse (ok/error + payload).
    """
    m = req.method
    sid = req.session_id
    params = req.params
    loop = asyncio.get_running_loop()

    # ── chat ──
    if m == ReqMethod.CHAT_SEND:
        if not sid:
            return AgentResponse(req.request_id, ok=False, error="missing session_id")
        gs = await asyncio.to_thread(sessions.get_or_hydrate, sid, loop)
        if gs is None:
            # Self-heal: the frontend may send a session_id from localStorage
            # that isn't in the DB (stale from a prior run / wiped DB). Revive
            # it as a fresh session under the same id instead of erroring, so
            # the user can always converse.
            gs = await asyncio.to_thread(sessions.create, "ws", loop, sid)
        text = params.get("content") or params.get("text") or ""
        if not isinstance(text, str):
            text = str(text)
        ok_flag = gs.post_message(text)
        debug("chat.send posted sid=%r seq_now=%r ok=%s",
              sid, getattr(gs.agent, "_seq", 0), ok_flag)
        if not ok_flag:
            return AgentResponse(req.request_id, ok=False,
                                 error="a turn is already in flight")
        return AgentResponse(req.request_id, payload={"ok": True})

    if m == ReqMethod.CHAT_INTERRUPT:
        if not sid:
            return AgentResponse(req.request_id, ok=False, error="missing session_id")
        gs = sessions.get(sid)
        if gs is None:
            return AgentResponse(req.request_id, ok=False, error="session not found")
        intent = params.get("intent", "cancel")
        if intent == "pause":
            gs.interrupt()
        elif intent == "supplement":
            new_input = params.get("new_input") or ""
            if new_input:
                gs.post_message(new_input)
        else:  # cancel / resume
            gs.interrupt()
        return AgentResponse(req.request_id, payload={"ok": True, "intent": intent})

    if m == ReqMethod.CHAT_USER_ANSWER:
        if not sid:
            return AgentResponse(req.request_id, ok=False, error="missing session_id")
        gs = sessions.get(sid)
        if gs is None:
            return AgentResponse(req.request_id, ok=False, error="session not found")
        rid = params.get("request_id", "")
        answers = params.get("answers") or []
        allow = True
        modify = None
        if isinstance(answers, list) and answers:
            a0 = answers[0]
            if isinstance(a0, dict):
                allow = bool(a0.get("allow", a0.get("selected", True)))
                modify = a0.get("modify")
        granted = gs.grant(rid, allow, modify)
        if not granted:
            return AgentResponse(req.request_id, ok=False,
                                 error="no pending permission with that id")
        return AgentResponse(req.request_id, payload={"ok": True})

    # ── session ──
    if m == ReqMethod.SESSION_LIST:
        rows = await asyncio.to_thread(_list_sessions, sessions)
        return AgentResponse(req.request_id, payload={"sessions": rows})

    if m == ReqMethod.SESSION_CREATE:
        transport = params.get("transport", "auto")
        gs = sessions.create(transport, loop=loop,
                             sid=params.get("session_id") or None)
        return AgentResponse(req.request_id,
                             payload={"session_id": gs.session_id, "transport": gs.transport})

    if m == ReqMethod.SESSION_SWITCH:
        # agent_core has no separate switch; hydrating on next chat.send is enough.
        if not sid:
            return AgentResponse(req.request_id, ok=False, error="missing session_id")
        gs = await asyncio.to_thread(sessions.get_or_hydrate, sid, loop)
        if gs is None:
            return AgentResponse(req.request_id, ok=False, error="session not found")
        return AgentResponse(req.request_id, payload=gs.meta())

    if m == ReqMethod.SESSION_DELETE:
        gs = sessions.get(sid) if sid else None
        if gs is not None:
            gs.interrupt()
            sessions.drop(gs.session_id)
        from agent_gateway import db
        await asyncio.to_thread(db.delete_session_row, sid)
        return AgentResponse(req.request_id, payload={"ok": True})

    if m == ReqMethod.SESSION_STATUS:
        if not sid:
            return AgentResponse(req.request_id, ok=False, error="missing session_id")
        gs = sessions.get(sid)
        if gs is None:
            return AgentResponse(req.request_id, ok=False, error="session not found")
        return AgentResponse(req.request_id, payload=_session_status(gs))

    if m == ReqMethod.SESSION_RENAME:
        # Persist a custom title by rewriting the chat record's title via db.
        if not sid:
            return AgentResponse(req.request_id, ok=False, error="missing session_id")
        title = str(params.get("title") or "")[:120]
        from agent_gateway import db
        gs = sessions.get(sid)
        record = gs.agent.record if gs is not None else None
        if record is not None:
            await asyncio.to_thread(db.save_chat_record, sid, record, _now(), title)
        return AgentResponse(req.request_id, payload={"ok": True, "title": title})

    # ── history ──
    if m == ReqMethod.HISTORY_GET:
        if not sid:
            return AgentResponse(req.request_id, ok=False, error="missing session_id")
        gs = await asyncio.to_thread(sessions.get_or_hydrate, sid, loop)
        if gs is None:
            # No persisted session. There's no session pipe to emit history
            # events on, so we can't stream a status:'done' frame here — mark
            # `empty` and let web_connect synthesize the done event directly on
            # the ws. Without that done frame, the frontend's
            # beginHistoryRestore never finalizes, isLoadingHistory stays true,
            # and the subsequent chat.final's setProcessing(false) is gated off
            # (useWebSocket.ts chat.final handler) — the three-dots spinner
            # persists even though the reply completes.
            return AgentResponse(req.request_id, payload={
                "accepted": True, "session_id": sid, "empty": True,
                "page_idx": (params.get("page_idx") if isinstance(params, dict) else None) or 1,
            })
        page_idx = (params.get("page_idx") if isinstance(params, dict) else None) or 1
        debug("history.get sid=%r seq_now=%r record_len=%d",
              sid, getattr(gs.agent, "_seq", 0), len(gs.agent.record or []))
        # Stream the conversation as history.message events (one per record),
        # finalized with a status:'done' frame — this is the contract the
        # jiuwenswarm frontend's beginHistoryRestore subscribes to. The RPC
        # response below is just an ack; the data arrives as events on the
        # session pipe (drained over the WS). Records are emitted newest-first
        # to match the frontend's entries.unshift() ordering.
        await asyncio.to_thread(_emit_history_stream, gs, page_idx)
        return AgentResponse(req.request_id, payload={
            "accepted": True, "session_id": sid, "page_idx": page_idx,
        })

    # ── config / models ──
    if m == ReqMethod.CONFIG_GET:
        return AgentResponse(req.request_id, payload=_config_get())

    if m == ReqMethod.CONFIG_SET:
        updates = {k: v for k, v in params.items() if k != "session_id"}
        resp = _config_set(updates)
        return AgentResponse(req.request_id, payload=resp)

    if m == ReqMethod.MODELS_LIST:
        cfg = model_config.get_config_masked()
        return AgentResponse(req.request_id, payload={
            "models": [{
                "model_name": cfg["model_id"],
                "api_base": cfg.get("base_url") or "",
                "api_key": cfg.get("api_key_masked") or "",
                "model_provider": "openai-compatible",
                "is_default": True,
                "alias": None,
            }],
            "active_model": cfg["model_id"],
        })

    if m == ReqMethod.MODELS_REPLACE_ALL:
        # Single-model config: accept the first entry's fields.
        models = params.get("models") or []
        if isinstance(models, list) and models:
            entry = models[0]
            if isinstance(entry, dict):
                model_config.write_config(
                    entry.get("model_name") or entry.get("model_id", ""),
                    entry.get("api_base"),
                    entry.get("api_key"),
                    entry.get("fallback_model"),
                )
        return AgentResponse(req.request_id, payload={"ok": True})

    # ── skills ──
    if m == ReqMethod.SKILLS_LIST:
        return AgentResponse(req.request_id, payload={"skills": code.scan_skills()})
    if m == ReqMethod.SKILLS_INSTALLED:
        return AgentResponse(req.request_id, payload={"skills": code.scan_skills()})
    if m == ReqMethod.SKILLS_GET:
        name = params.get("name", "")
        skill = code.load_skill(name) if name else None
        return AgentResponse(req.request_id, payload={"skill": skill})

    # ── agents ──
    if m == ReqMethod.AGENTS_LIST:
        return AgentResponse(req.request_id, payload={"agents": code.list_agents()})
    if m == ReqMethod.AGENTS_GET:
        name = params.get("name", "")
        ag = code.get_agent(name) if name else None
        return AgentResponse(req.request_id, payload={"agent": ag})
    if m == ReqMethod.AGENTS_CREATE:
        try:
            ag = code.save_agent(params.get("name", ""), params.get("description", ""),
                                 params.get("prompt", ""), params.get("model"),
                                 params.get("tools") or [])
        except ValueError as e:
            return AgentResponse(req.request_id, ok=False, error=str(e))
        return AgentResponse(req.request_id, payload=ag)
    if m == ReqMethod.AGENTS_UPDATE:
        try:
            ag = code.save_agent(params.get("name", ""), params.get("description", ""),
                                 params.get("prompt", ""), params.get("model"),
                                 params.get("tools") or [])
        except ValueError as e:
            return AgentResponse(req.request_id, ok=False, error=str(e))
        return AgentResponse(req.request_id, payload=ag)
    if m == ReqMethod.AGENTS_DELETE:
        try:
            ok_flag = code.delete_agent(params.get("name", ""))
        except ValueError as e:
            return AgentResponse(req.request_id, ok=False, error=str(e))
        if not ok_flag:
            return AgentResponse(req.request_id, ok=False, error="agent not found")
        return AgentResponse(req.request_id, payload={"ok": True})

    # ── path / files ──
    if m == ReqMethod.PATH_GET:
        return AgentResponse(req.request_id, payload={"path": str(code.workdir())})
    if m == ReqMethod.FILES_LIST:
        import os
        path = params.get("path") or str(code.workdir())
        try:
            entries = sorted(os.listdir(path))
        except OSError as e:
            return AgentResponse(req.request_id, ok=False, error=str(e))
        return AgentResponse(req.request_id, payload={"files": entries, "path": path})

    # ── tts (stub — agent_core has no TTS; return empty so the UI degrades) ──
    if m == ReqMethod.TTS_SYNTHESIZE:
        return AgentResponse(req.request_id, payload={"success": False, "reason": "no_tts"})

    # ── commands (slash) — handled by injecting as a user message ──
    if m in (ReqMethod.COMMAND_COMPACT, ReqMethod.COMMAND_CONTEXT,
             ReqMethod.COMMAND_MODEL):
        if not sid:
            return AgentResponse(req.request_id, ok=False, error="missing session_id")
        gs = sessions.get(sid)
        if gs is None:
            return AgentResponse(req.request_id, ok=False, error="session not found")
        text = f"/{m.value.split('.')[1]}"
        gs.post_message(text)
        return AgentResponse(req.request_id, payload={"ok": True})

    # ── channel / heartbeat ──
    if m == ReqMethod.CHANNEL_GET:
        return AgentResponse(req.request_id, payload={"channel_id": req.channel_id})
    if m == ReqMethod.HEARTBEAT_PING:
        return AgentResponse(req.request_id, payload={"ok": True, "t": _now()})

    return AgentResponse(req.request_id, ok=False, error=f"unhandled method {m.value}")


# ── helpers ──

def _now() -> float:
    import time
    return time.time()


def _stringify_content(content: Any) -> str:
    """Flatten agent_core message content (str or list of blocks) to a string.

    Blocks may be dicts (hydrated from DB/JSON) or SimpleNamespace instances
    (_TextBlock/_ToolUseBlock) when the session is live in memory, so use
    _block_type/_block_attr instead of assuming dict shape."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if code._block_type(b) == "text":
                parts.append(code._block_attr(b, "text", "") or "")
        return "".join(parts)
    return str(content) if content is not None else ""


def _emit_history_stream(gs, page_idx: int) -> None:
    """Convert the agent_core chat record into jiuwenswarm history records and
    emit them as `history.message` events on the session pipe, newest-first,
    followed by a `status:'done'` frame. The frontend's beginHistoryRestore
    subscribes to history.message events (not the RPC response) and rebuilds
    the conversation from them.

    Record shapes (see frontend parseHistoryTimelineEntry):
      user       → {role:'user',    content:<str>, timestamp}
      assistant  → {role:'assistant', event_type:'chat.final',      content:<str>, timestamp}
                 | {role:'assistant', event_type:'chat.tool_call',  event_payload:{id,name,arguments}, timestamp}
                 | {role:'assistant', event_type:'chat.tool_result',event_payload:{id,tool_call_id,tool_name,name,result,success}, timestamp}
    """
    import time as _time
    record = gs.agent.record or []
    tool_names: dict[str, str] = {}
    # Base timestamp: spread records across time so display ordering is stable.
    base = float(gs.created_at) if gs.created_at else _time.time()
    records: list[dict] = []
    for i, msg in enumerate(record):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        ts = base + i  # monotonic; oldest gets smallest timestamp
        if role == "user":
            if isinstance(content, str):
                if not content.strip():
                    continue
                records.append({"role": "user", "content": content, "timestamp": ts})
            elif isinstance(content, list):
                # Multimodal user turn: text blocks → user content; tool_result
                # blocks → assistant tool_result records (correlated by id).
                text_parts = []
                for b in content:
                    bt = code._block_type(b)
                    if bt == "text":
                        text_parts.append(code._block_attr(b, "text", "") or "")
                    elif bt == "tool_result":
                        tid = code._block_attr(b, "tool_use_id") or code._block_attr(b, "id")
                        tname = tool_names.get(tid) if isinstance(tid, str) else None
                        records.append({
                            "role": "assistant",
                            "event_type": "chat.tool_result",
                            "event_payload": {
                                "id": tid,
                                "tool_call_id": tid,
                                "tool_name": tname,
                                "name": tname,
                                "result": _stringify_content(code._block_attr(b, "content")),
                                "success": not bool(code._block_attr(b, "is_error")),
                            },
                            "timestamp": ts,
                        })
                joined = "".join(text_parts).strip()
                if joined:
                    records.append({"role": "user", "content": joined, "timestamp": ts})
        elif role == "assistant":
            if isinstance(content, str):
                if content.strip():
                    records.append({"role": "assistant", "event_type": "chat.final",
                                     "content": content, "timestamp": ts})
            elif isinstance(content, list):
                for b in content:
                    bt = code._block_type(b)
                    if bt == "text":
                        txt = code._block_attr(b, "text", "") or ""
                        if txt.strip():
                            records.append({"role": "assistant", "event_type": "chat.final",
                                            "content": txt, "timestamp": ts})
                    elif bt == "tool_use":
                        tid = code._block_attr(b, "id")
                        tname = code._block_attr(b, "name")
                        if isinstance(tid, str) and isinstance(tname, str):
                            tool_names[tid] = tname
                        records.append({
                            "role": "assistant",
                            "event_type": "chat.tool_call",
                            "event_payload": {
                                "id": tid,
                                "name": tname,
                                "arguments": code._block_attr(b, "input", {}),
                            },
                            "timestamp": ts,
                        })
    # Newest-first: the frontend prepends each received entry (entries.unshift),
    # so emitting newest-first yields a chronologically ordered display.
    records.reverse()
    sid = gs.session_id
    for rec in records:
        gs.agent.emit("history_message", {
            "message": rec,
            "session_id": sid,
            "page_idx": page_idx,
            "total_pages": 1,
        })
    gs.agent.emit("history_message", {
        "status": "done",
        "session_id": sid,
        "page_idx": page_idx,
        "total_pages": 1,
    })
    debug("history emitted sid=%r records=%d seq_now=%r",
          sid, len(records), getattr(gs.agent, "_seq", 0))


def _list_sessions(sessions) -> list[dict]:
    """Merge DB rows with live sessions (live is at least as fresh).

    Returns jiuwenswarm Session-shape objects: session_id, title, project_path,
    mode, status, message_count, created_at, updated_at (plus transport/last_activity
    for our own use). The frontend Session interface requires these fields.
    """
    from agent_gateway import db
    import code as _code
    project_path = str(_code.workdir())
    rows = db.list_session_rows()
    by_sid: dict[str, dict] = {}
    for r in rows:
        rec = r.get("chat_record") or []
        by_sid[r["session_id"]] = _session_shape(
            session_id=r["session_id"],
            title=r["title"],
            project_path=project_path,
            created_at=r["created_at"],
            updated_at=r["last_activity"],
            message_count=len(rec),
            transport=r["transport"],
            last_activity=r["last_activity"],
        )
    for gs in sessions.all():
        meta = gs.meta()
        by_sid[gs.session_id] = _session_shape(
            session_id=gs.session_id,
            title=meta.get("title", ""),
            project_path=project_path,
            created_at=meta.get("created_at", 0),
            updated_at=meta.get("last_activity", meta.get("created_at", 0)),
            message_count=meta.get("history_len", 0),
            transport=meta.get("transport", "auto"),
            last_activity=meta.get("last_activity", 0),
        )
    return sorted(by_sid.values(),
                  key=lambda m: m.get("last_activity", 0), reverse=True)


def _session_shape(*, session_id: str, title: str, project_path: str,
                   created_at: Any, updated_at: Any, message_count: int,
                   transport: str, last_activity: Any) -> dict:
    """Coerce to the jiuwenswarm Session interface (string timestamps, defaults)."""
    return {
        "session_id": session_id,
        "title": title or "",
        "project_path": project_path,
        "mode": "agent.fast",
        "status": "active",
        "message_count": message_count,
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
        "is_active": False,
        "is_processing": False,
        # myAgent-internal fields (harmless extras the frontend ignores).
        "transport": transport,
        "last_activity": last_activity,
        "history_len": message_count,
    }


def _iso(ts: Any) -> str:
    """Unix seconds (float/int) → ISO string; pass strings through."""
    if isinstance(ts, str):
        return ts
    try:
        import datetime
        return datetime.datetime.utcfromtimestamp(float(ts)).isoformat() + "Z"
    except (TypeError, ValueError):
        return ""


def _session_status(gs) -> dict:
    return {
        "session_id": gs.session_id,
        "transport": gs.transport,
        "active_sinks": [type(s).__name__ for s in gs.agent.sinks],
        "last_seq": gs.agent._seq,
        "buffered": gs.pipe.count(),
        "worker_alive": gs._worker is not None and gs._worker.is_alive(),
        "history_len": len(gs.agent.record),
    }


def _config_get() -> dict:
    cfg = model_config.get_config_masked()
    return {
        "model_id": cfg["model_id"],
        "base_url": cfg.get("base_url"),
        "api_key_masked": cfg.get("api_key_masked"),
        "fallback_model": cfg.get("fallback_model"),
        "a2ui_enabled": False,
    }


def _config_set(updates: dict) -> dict:
    """Apply config updates. Only model fields are writable in myAgent."""
    model_id = updates.get("model_id")
    if model_id is not None:
        model_config.write_config(
            model_id,
            updates.get("base_url"),
            updates.get("api_key"),
            updates.get("fallback_model"),
        )
        return {"updated": ["model_id"], "applied_without_restart": True}
    return {"updated": [], "applied_without_restart": True}
