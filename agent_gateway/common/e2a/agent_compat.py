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
from ... import skill_marketplaces as mp


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
        # ask_user / permission answers arrive as chat.send{request_id, answers,
        # source} (see frontend sendUserAnswer). Route them to the matching
        # future resolver instead of posting an empty user message.
        rid = params.get("request_id")
        if rid:
            answers = params.get("answers") or []
            source = params.get("source") or ""
            if source == "ask_user_interrupt":
                ok_ans = gs.respond_ask(rid, answers)
            else:
                # permission_interrupt / confirm_interrupt / etc. → grant.
                allow = True
                modify = None
                if isinstance(answers, list) and answers:
                    a0 = answers[0]
                    if isinstance(a0, dict):
                        allow = bool(a0.get("allow", a0.get("selected", True)))
                        modify = a0.get("modify")
                        # UserQuestionModal submits {selected_options: [label]}
                        # where label is "Allow"/"Deny" (see wire.py's
                        # permission_request mapping). Interpret it so the
                        # user's choice actually reaches the FuturePermission.
                        so = a0.get("selected_options")
                        if isinstance(so, list) and so:
                            label = str(so[0]).strip().lower()
                            if label in ("deny", "拒绝", "no", "n", "false"):
                                allow = False
                            elif label in ("allow", "允许", "yes", "y", "true"):
                                allow = True
                ok_ans = gs.grant(rid, allow, modify)
            if not ok_ans:
                return AgentResponse(req.request_id, ok=False,
                                     error="no pending question with that id")
            return AgentResponse(req.request_id, payload={"ok": True})
        text = params.get("content") or params.get("text") or ""
        if not isinstance(text, str):
            text = str(text)
        # ── 集群模式 (Cluster Mode): route mode='team' to the team engine.
        # The gateway doesn't run the team itself — it flips a context flag and
        # the system-prompt directive (prompt.py) tells the agent to call
        # start_team(team_name, task=<user request>) and orchestrate via
        # wait/check_inbox/review_plan. Team selection is automatic:
        # 0 teams → error (configure one first); 1 → use it; >1 → pass the
        # list to the agent and let it pick based on the task.
        if req.mode == Mode.TEAM:
            from agent_core.agents import list_team_names
            names = list_team_names()
            if not names:
                return AgentResponse(req.request_id, ok=False,
                                     error="请先在配置中创建一个团队（集群模式需要一个团队定义）")
            gs.agent.context["team_mode"] = names[0] if len(names) == 1 else {"teams": names}
        else:
            gs.agent.context.pop("team_mode", None)
        # Persist the mode per-session so restoring it from the sidebar reports
        # the right mode instead of always falling back to agent.fast.
        try:
            from agent_gateway import db
            db.save_session_mode(sid, _mode_str(req) or "agent.fast")
        except Exception:
            pass
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
            debug("chat.interrupt sid=%r NOT FOUND (not in this replica's registry)", sid)
            return AgentResponse(req.request_id, ok=False, error="session not found")
        intent = params.get("intent", "cancel")
        debug("chat.interrupt sid=%r intent=%r", sid, intent)
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
                so = a0.get("selected_options")
                if isinstance(so, list) and so:
                    label = str(so[0]).strip().lower()
                    if label in ("deny", "拒绝", "no", "n", "false"):
                        allow = False
                    elif label in ("allow", "允许", "yes", "y", "true"):
                        allow = True
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
        from agent_gateway.sessions import cleanup_session_artifacts
        await asyncio.to_thread(db.delete_session_row, sid)
        await asyncio.to_thread(cleanup_session_artifacts, sid)
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

    # ── permissions (security panel: per-tool allow/ask/deny) ──
    if m == ReqMethod.PERMISSIONS_TOOLS_GET:
        from agent_core import permissions
        return AgentResponse(req.request_id, payload=permissions.get_policy())
    if m == ReqMethod.PERMISSIONS_TOOLS_UPDATE:
        from agent_core import permissions
        tool = str(params.get("tool") or "").strip()
        level = str(params.get("level") or "").strip().lower()
        if not tool or level not in permissions.LEVELS:
            return AgentResponse(req.request_id, ok=False,
                                 error="invalid tool or level")
        return AgentResponse(req.request_id,
                             payload=permissions.set_tool_level(tool, level))
    if m == ReqMethod.PERMISSIONS_TOOLS_DELETE:
        from agent_core import permissions
        tool = str(params.get("tool") or "").strip()
        return AgentResponse(req.request_id,
                             payload=permissions.delete_tool(tool))

    if m == ReqMethod.CONFIG_SET:
        updates = {k: v for k, v in params.items() if k != "session_id"}
        resp = _config_set(updates)
        # Flat agent/team keys (legacy save path): reconstruct and persist.
        if any(k.startswith("agent_name_") or k.startswith("agent_") and k.endswith("_name")
               or k.startswith("team_") or k.startswith("team_name_") for k in updates):
            try:
                agents, team = code.agents_flat_to_structured(updates)
                if agents or team:
                    code.write_agents_config(agents, team)
                    resp.setdefault("updated", [])
                    if agents:
                        resp["updated"].append("agents")
                    if team:
                        resp["updated"].append("team")
            except Exception:  # noqa: BLE001
                pass
        return AgentResponse(req.request_id, payload=resp)

    if m == ReqMethod.MODELS_LIST:
        models = model_config.get_models()
        active = model_config.get_config()["model_id"]
        return AgentResponse(req.request_id, payload={
            "models": models,
            "active_model": active,
        })

    if m == ReqMethod.MODELS_REPLACE_ALL:
        # Multi-model list: persist the full list; the primary entry drives the
        # active model (top-level model_id/base_url/api_key).
        models = params.get("models") or []
        if isinstance(models, list):
            model_config.write_models(models)
        return AgentResponse(req.request_id, payload={"ok": True, "applied_without_restart": True})

    if m == ReqMethod.CONFIG_SAVE_ALL:
        # Unified save: {config?, models?, agents?, team?}. myAgent applies
        # config in-place WITHOUT restarting the process, so we always return
        # applied_without_restart=True — the frontend's restart modal then
        # flips to success immediately instead of hanging on "waiting for
        # backend to restart and reconnect" (the WS never drops because
        # nothing restarted).
        updated: list[str] = []
        models = params.get("models")
        if isinstance(models, list):
            model_config.write_models(models)
            updated.append("models")
        cfg_updates = params.get("config")
        if isinstance(cfg_updates, dict) and cfg_updates:
            r = _config_set({k: v for k, v in cfg_updates.items() if k != "session_id"})
            if r.get("updated"):
                updated.extend(r["updated"])
        # agents/team (structured): persist to agents_config.json and sync
        # subagent defs so they appear in the catalog and are usable via task.
        agents_payload = params.get("agents")
        team_payload = params.get("team")
        if agents_payload is not None or team_payload is not None:
            try:
                code.write_agents_config(agents_payload, team_payload)
                updated.append("agents")
            except Exception:  # noqa: BLE001
                pass
        return AgentResponse(req.request_id, payload={
            "updated": updated,
            "applied_without_restart": True,
        })

    if m == ReqMethod.CONFIG_VALIDATE_MODEL:
        # Connectivity + model-id probe: build a one-off OpenAI client with the
        # supplied fields and call GET /models (client.models.list). This
        # verifies the host is reachable, the api_key authenticates, AND the
        # supplied model_id is one the endpoint actually serves — all without
        # consuming any tokens (no chat completion). Returns ok=True only if
        # model_id is found in the listed ids; any error (bad key, wrong base,
        # unreachable host, unknown model) → ok=False with the underlying
        # message so the UI's Test button reflects reality instead of always
        # succeeding.
        # The model card masks the saved api_key as "***"/"sk-***xxxx"; when the
        # caller sends that placeholder (or leaves a field blank), fall back to
        # the currently persisted value so Test works on the unedited config.
        saved = model_config.get_config()
        api_base = (params.get("api_base") or "").strip() or saved.get("base_url") or ""
        raw_key = (params.get("api_key") or "").strip()
        if not raw_key or "***" in raw_key:
            api_key = saved.get("api_key") or ""
        else:
            api_key = raw_key
        model_id = (params.get("model") or params.get("model_name") or "").strip() \
            or saved.get("model_id") or ""
        if not api_base or not api_key or not model_id:
            return AgentResponse(req.request_id, ok=False,
                                 error="api_base, api_key and model are required")
        try:
            from openai import OpenAI
            client = OpenAI(base_url=api_base, api_key=api_key, timeout=30)
            listed = [m_obj.id for m_obj in client.models.list().data]
        except Exception as exc:  # noqa: BLE001
            return AgentResponse(req.request_id, ok=False,
                                 error=f"{type(exc).__name__}: {exc}")
        if not listed:
            return AgentResponse(req.request_id, ok=False,
                                 error="endpoint returned no models; cannot verify model id")
        if model_id not in listed:
            # Surface a few candidates so the user can correct the name; cap to
            # avoid dumping hundreds of ids into the UI toast.
            sample = ", ".join(listed[:12])
            more = f" …(+{len(listed) - 12} more)" if len(listed) > 12 else ""
            return AgentResponse(req.request_id, ok=False,
                                 error=f"model '{model_id}' not in endpoint's model list. "
                                       f"Available: {sample}{more}")
        return AgentResponse(req.request_id, payload={"ok": True, "model_id": model_id})

    # ── skills ──
    if m == ReqMethod.SKILLS_LIST:
        return AgentResponse(req.request_id, payload={"skills": code.scan_skills()})
    if m == ReqMethod.SKILLS_INSTALLED:
        return AgentResponse(req.request_id, payload={"skills": code.scan_skills()})
    if m == ReqMethod.SKILLS_GET:
        name = params.get("name", "")
        skill = code.get_skill(name) if name else None
        return AgentResponse(req.request_id, payload=skill or {})
    if m == ReqMethod.SKILLS_TOGGLE:
        return AgentResponse(req.request_id, payload=code.set_skill_enabled(
            params.get("name", ""), bool(params.get("enabled", True))))
    if m == ReqMethod.SKILLS_UNINSTALL:
        return AgentResponse(req.request_id, payload=code.uninstall_skill(params.get("name", "")))
    if m == ReqMethod.SKILLS_INSTALL:
        return AgentResponse(req.request_id, payload=code.install_skill(
            params.get("spec", ""), bool(params.get("force", False))))
    if m == ReqMethod.SKILLS_IMPORT_LOCAL:
        return AgentResponse(req.request_id, payload=code.import_local_skill(
            params.get("path", ""), bool(params.get("force", False))))
    if m == ReqMethod.SKILLS_MARKETPLACE_LIST:
        return AgentResponse(req.request_id, payload=code.list_marketplaces())
    # ── online marketplaces (clawhub.ai / SkillNet via GitHub search) ──
    if m == ReqMethod.SKILLS_CLAWHUB_SEARCH:
        res = await asyncio.to_thread(mp.clawhub_search, params.get("q", ""), int(params.get("limit", 50)))
        return AgentResponse(req.request_id, payload=res)
    if m == ReqMethod.SKILLS_CLAWHUB_DOWNLOAD:
        res = await asyncio.to_thread(mp.clawhub_download, params.get("slug", ""), bool(params.get("force", False)), params.get("meta"))
        return AgentResponse(req.request_id, payload=res)
    if m == ReqMethod.SKILLS_CLAWHUB_GET_TOKEN:
        return AgentResponse(req.request_id, payload=mp.clawhub_get_token())
    if m == ReqMethod.SKILLS_CLAWHUB_SET_TOKEN:
        return AgentResponse(req.request_id, payload=mp.clawhub_set_token(params.get("token", "")))
    if m == ReqMethod.SKILLS_SKILLNET_SEARCH:
        res = await asyncio.to_thread(mp.skillnet_search, params.get("q", ""), int(params.get("limit", 20)))
        return AgentResponse(req.request_id, payload=res)
    if m == ReqMethod.SKILLS_SKILLNET_INSTALL:
        res = await asyncio.to_thread(mp.skillnet_install, params.get("url", ""), bool(params.get("force", False)), params.get("meta"))
        return AgentResponse(req.request_id, payload=res)
    if m == ReqMethod.SKILLS_SKILLNET_INSTALL_STATUS:
        return AgentResponse(req.request_id, payload=mp.skillnet_install_status(params.get("install_id", "")))
    if m == ReqMethod.SKILLS_SKILLNET_EVALUATE:
        return AgentResponse(req.request_id, payload=mp.skillnet_evaluate(params.get("url", "")))
    if m == ReqMethod.SKILLS_TEAMSKILLS_SEARCH:
        res = await asyncio.to_thread(mp.teamskills_search, params.get("q", ""), int(params.get("limit", 50)))
        return AgentResponse(req.request_id, payload=res)
    if m == ReqMethod.SKILLS_TEAMSKILLS_INSTALL:
        res = await asyncio.to_thread(mp.teamskills_install, params.get("asset_id", ""), bool(params.get("force", False)))
        return AgentResponse(req.request_id, payload=res)
    if m == ReqMethod.SKILLS_TEAMSKILLS_INFO:
        return AgentResponse(req.request_id, payload=mp.teamskills_info())
    if m == ReqMethod.SKILLS_SKILLHUB_SEARCH:
        res = await asyncio.to_thread(mp.skillhub_search, params.get("q", ""), int(params.get("limit", 50)))
        return AgentResponse(req.request_id, payload=res)
    if m == ReqMethod.SKILLS_SKILLHUB_INSTALL:
        res = await asyncio.to_thread(mp.skillhub_install, params.get("asset_id", ""), bool(params.get("force", False)), params.get("meta"))
        return AgentResponse(req.request_id, payload=res)
    if m == ReqMethod.SKILLS_SKILLHUB_INFO:
        return AgentResponse(req.request_id, payload=mp.skillhub_info())

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

    # ── runtime status: process RSS + used % for the ToolPanel status card ──
    if m == ReqMethod.MEMORY_COMPUTE:
        return AgentResponse(req.request_id, payload=_memory_compute())

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


def _memory_compute() -> dict:
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
            mode=r.get("mode", "agent.fast"),
        )
    for gs in sessions.all():
        meta = gs.meta()
        # Live session mode: prefer the DB-persisted mode (authoritative, set
        # on every chat.send); fall back to the in-memory context flag (set by
        # chat.send when req.mode == TEAM) so a just-started team session
        # reports "team" before the DB row is written.
        db_mode = by_sid.get(gs.session_id, {}).get("mode")
        live_mode = db_mode or ("team" if gs.agent.context.get("team_mode") else "agent.fast")
        by_sid[gs.session_id] = _session_shape(
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


def _session_shape(*, session_id: str, title: str, project_path: str,
                   created_at: Any, updated_at: Any, message_count: int,
                   transport: str, last_activity: Any,
                   mode: str = "agent.fast") -> dict:
    """Coerce to the jiuwenswarm Session interface (string timestamps, defaults)."""
    return {
        "session_id": session_id,
        "title": title or "",
        "project_path": project_path,
        "mode": mode or "agent.fast",
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
    out = {
        "model_id": cfg["model_id"],
        "base_url": cfg.get("base_url"),
        "api_key_masked": cfg.get("api_key_masked"),
        "fallback_model": cfg.get("fallback_model"),
        "a2ui_enabled": False,
    }
    # Merge the Agent-tab flat keys (agent_name_${i}, team_${i}_*, …) so the
    # ConfigPanel Agent tab populates from the persisted agents_config.json.
    try:
        out.update(code.agents_flat_config())
    except Exception:  # noqa: BLE001
        pass
    # Security-tab group keys. The ConfigPanel forms a "permissions" group from
    # `permissions_enabled` and a "memory" group from the memory_forbidden_*
    # keys. Values come from the persisted security policy (permissions.py),
    # not hardcoded seeds, so toggles/patterns survive a reload.
    try:
        from agent_core import permissions
        out["permissions_enabled"] = "true" if permissions.is_enabled() else "false"
        mf = permissions.get_memory_forbidden()
        out["memory_forbidden_enabled"] = "true" if mf.get("enabled") else "false"
        out["memory_forbidden_description"] = mf.get("pattern", "")
    except Exception:  # noqa: BLE001
        out.setdefault("permissions_enabled", "true")
        out.setdefault("memory_forbidden_enabled", "false")
        out.setdefault("memory_forbidden_description", "")
    return out


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
    # Security-tab keys persist into the security policy (permissions.py),
    # not model_config. Handle them separately so toggles/patterns stick.
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
    except Exception:  # noqa: BLE001
        pass
    if updated:
        return {"updated": updated, "applied_without_restart": True}
    return {"updated": [], "applied_without_restart": True}
