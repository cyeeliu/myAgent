"""Shared helper functions for agent_compat handlers.

Extracted from the original ``agent_compat.py`` to keep handler modules thin.
"""
from __future__ import annotations

import time
from typing import Any

from agent_core import _block_type, _block_attr, workdir, agents_flat_config

from agent_core import model_config
from ..schema.agent import AgentResponse
from ...debug import debug


def now() -> float:
    return time.time()


def memory_compute() -> dict:
    """Process RSS (MB) + used % of system RAM for the ToolPanel status card.

    Stdlib only (resource + /proc/meminfo) so no psutil dep. ru_maxrss is KB on
    Linux. Never raises — on any failure the card degrades to '—' (nulls)."""
    try:
        import resource
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_mb = round(rss_kb / 1024.0, 1)
    except Exception:
        rss_mb = None
    used_percent = None
    try:
        if rss_mb is not None:
            total_kb = None
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            total_kb = int(line.split()[1])
                            break
            except Exception:
                total_kb = None
            if total_kb and total_kb > 0:
                used_percent = round((rss_kb / total_kb) * 100.0, 1)
    except Exception:
        used_percent = None
    return {"rss_mb": rss_mb, "used_percent": used_percent}


def stringify_content(content: Any) -> str:
    """Flatten agent_core message content (str or list of blocks) to a string.

    Blocks may be dicts (hydrated from DB/JSON) or SimpleNamespace instances
    (_TextBlock/_ToolUseBlock) when the session is live in memory, so use
    _block_type/_block_attr instead of assuming dict shape."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if _block_type(b) == "text":
                parts.append(_block_attr(b, "text", "") or "")
        return "".join(parts)
    return str(content) if content is not None else ""


def emit_history_stream(gs, page_idx: int) -> None:
    """Convert the agent_core chat record into myagent history records and
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
    record = gs.agent.record or []
    tool_names: dict[str, str] = {}
    base = float(gs.created_at) if gs.created_at else time.time()
    records: list[dict] = []
    for i, msg in enumerate(record):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        ts = base + i
        if role == "user":
            if isinstance(content, str):
                if not content.strip():
                    continue
                records.append({"role": "user", "content": content, "timestamp": ts})
            elif isinstance(content, list):
                text_parts = []
                for b in content:
                    bt = _block_type(b)
                    if bt == "text":
                        text_parts.append(_block_attr(b, "text", "") or "")
                    elif bt == "tool_result":
                        tid = _block_attr(b, "tool_use_id") or _block_attr(b, "id")
                        tname = tool_names.get(tid) if isinstance(tid, str) else None
                        records.append({
                            "role": "assistant",
                            "event_type": "chat.tool_result",
                            "event_payload": {
                                "id": tid,
                                "tool_call_id": tid,
                                "tool_name": tname,
                                "name": tname,
                                "result": stringify_content(_block_attr(b, "content")),
                                "success": not bool(_block_attr(b, "is_error")),
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
                    bt = _block_type(b)
                    if bt == "text":
                        txt = _block_attr(b, "text", "") or ""
                        if txt.strip():
                            records.append({"role": "assistant", "event_type": "chat.final",
                                            "content": txt, "timestamp": ts})
                    elif bt == "tool_use":
                        tid = _block_attr(b, "id")
                        tname = _block_attr(b, "name")
                        if isinstance(tid, str) and isinstance(tname, str):
                            tool_names[tid] = tname
                        records.append({
                            "role": "assistant",
                            "event_type": "chat.tool_call",
                            "event_payload": {
                                "id": tid,
                                "name": tname,
                                "arguments": _block_attr(b, "input", {}),
                            },
                            "timestamp": ts,
                        })
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


def list_sessions(sessions) -> list[dict]:
    """Merge DB rows with live sessions (live is at least as fresh).

    Returns myagent Session-shape objects: session_id, title, project_path,
    mode, status, message_count, created_at, updated_at (plus transport/last_activity
    for our own use). The frontend Session interface requires these fields.
    """
    from agent_gateway import db
    project_path = str(workdir())
    rows = db.list_session_rows()
    by_sid: dict[str, dict] = {}
    for r in rows:
        rec = r.get("chat_record") or []
        by_sid[r["session_id"]] = session_shape(
            session_id=r["session_id"],
            title=r["title"],
            project_path=project_path,
            created_at=r["created_at"],
            updated_at=r["last_activity"],
            message_count=len(rec),
            transport=r["transport"],
            last_activity=r["last_activity"],
            mode=r.get("mode", "agent.fast"),
        )
    for gs in sessions.all():
        meta = gs.meta()
        db_mode = by_sid.get(gs.session_id, {}).get("mode")
        live_mode = db_mode or ("team" if gs.agent.context.get("team_mode") else "agent.fast")
        by_sid[gs.session_id] = session_shape(
            session_id=gs.session_id,
            title=meta.get("title", ""),
            project_path=project_path,
            created_at=meta.get("created_at", 0),
            updated_at=meta.get("last_activity", meta.get("created_at", 0)),
            message_count=meta.get("history_len", 0),
            transport=meta.get("transport", "auto"),
            last_activity=meta.get("last_activity", 0),
            mode=live_mode,
        )
    return sorted(by_sid.values(),
                  key=lambda m: m.get("last_activity", 0), reverse=True)


def session_shape(*, session_id: str, title: str, project_path: str,
                  created_at: Any, updated_at: Any, message_count: int,
                  transport: str, last_activity: Any,
                  mode: str = "agent.fast") -> dict:
    """Coerce to the myagent Session interface (string timestamps, defaults)."""
    return {
        "session_id": session_id,
        "title": title or "",
        "project_path": project_path,
        "mode": mode or "agent.fast",
        "status": "active",
        "message_count": message_count,
        "created_at": iso(created_at),
        "updated_at": iso(updated_at),
        "is_active": False,
        "is_processing": False,
        "transport": transport,
        "last_activity": last_activity,
        "history_len": message_count,
    }


def iso(ts: Any) -> str:
    """Unix seconds (float/int) → ISO string; pass strings through."""
    if isinstance(ts, str):
        return ts
    try:
        import datetime
        return datetime.datetime.utcfromtimestamp(float(ts)).isoformat() + "Z"
    except (TypeError, ValueError):
        return ""


def session_status(gs) -> dict:
    return {
        "session_id": gs.session_id,
        "transport": gs.transport,
        "active_sinks": [type(s).__name__ for s in gs.agent.sinks],
        "last_seq": gs.agent._seq,
        "buffered": gs.pipe.count(),
        "worker_alive": gs._worker is not None and gs._worker.is_alive(),
        "history_len": len(gs.agent.record),
    }


def config_get() -> dict:
    cfg = model_config.get_config_masked()
    out = {
        "model_id": cfg["model_id"],
        "base_url": cfg.get("base_url"),
        "api_key_masked": cfg.get("api_key_masked"),
        "fallback_model": cfg.get("fallback_model"),
        "a2ui_enabled": False,
    }
    try:
        out.update(agents_flat_config())
    except Exception:
        pass
    try:
        from agent_core import permissions
        out["permissions_enabled"] = "true" if permissions.is_enabled() else "false"
        mf = permissions.get_memory_forbidden()
        out["memory_forbidden_enabled"] = "true" if mf.get("enabled") else "false"
        out["memory_forbidden_description"] = mf.get("pattern", "")
    except Exception:
        out.setdefault("permissions_enabled", "true")
        out.setdefault("memory_forbidden_enabled", "false")
        out.setdefault("memory_forbidden_description", "")
    return out


def config_set(updates: dict) -> dict:
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
    updated: list[str] = []
    try:
        from agent_core import permissions
        if "permissions_enabled" in updates:
            permissions.set_enabled(
                str(updates.get("permissions_enabled", "")).lower()
                in ("true", "1", "yes"))
            updated.append("permissions_enabled")
        mf_en = updates.get("memory_forbidden_enabled")
        mf_pat = updates.get("memory_forbidden_description")
        if mf_en is not None or mf_pat is not None:
            cur = permissions.get_memory_forbidden()
            en = (str(mf_en).lower() in ("true", "1", "yes")
                  if mf_en is not None else bool(cur.get("enabled", False)))
            pat = str(mf_pat) if mf_pat is not None else str(cur.get("pattern", ""))
            permissions.set_memory_forbidden(en, pat)
            if mf_en is not None:
                updated.append("memory_forbidden_enabled")
            if mf_pat is not None:
                updated.append("memory_forbidden_description")
    except Exception:
        pass
    if updated:
        return {"updated": updated, "applied_without_restart": True}
    return {"updated": [], "applied_without_restart": True}
