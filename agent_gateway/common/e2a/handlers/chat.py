"""Chat handlers: chat.send, chat.interrupt, chat.user_answer."""
from __future__ import annotations

import asyncio

from ...schema.agent import AgentResponse
from ...schema.message import ReqMethod, Mode
from ....debug import debug
from ..dispatcher import handler, HandlerContext


@handler(ReqMethod.CHAT_SEND)
async def chat_send(req, ctx: HandlerContext):
    sessions = ctx.sessions
    sid = req.session_id
    params = req.params
    if not sid:
        return AgentResponse(req.request_id, ok=False, error="missing session_id")
    gs = await asyncio.to_thread(sessions.get_or_hydrate, sid, ctx.loop)
    if gs is None:
        gs = await asyncio.to_thread(sessions.create, "ws", ctx.loop, sid)
    rid = params.get("request_id")
    if rid:
        answers = params.get("answers") or []
        source = params.get("source") or ""
        if source == "ask_user_interrupt":
            ok_ans = gs.respond_ask(rid, answers)
        else:
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
            ok_ans = gs.grant(rid, allow, modify)
        if not ok_ans:
            return AgentResponse(req.request_id, ok=False,
                                 error="no pending question with that id")
        return AgentResponse(req.request_id, payload={"ok": True})
    text = params.get("content") or params.get("text") or ""
    if not isinstance(text, str):
        text = str(text)
    # ── 集群模式 (Cluster Mode) ──
    if req.mode == Mode.TEAM:
        from agent_core.agents import list_team_names
        names = list_team_names()
        if not names:
            return AgentResponse(req.request_id, ok=False,
                                 error="请先在配置中创建一个团队（集群模式需要一个团队定义）")
        gs.agent.context["team_mode"] = names[0] if len(names) == 1 else {"teams": names}
    else:
        gs.agent.context.pop("team_mode", None)
    # ── 规划模式 (Plan Mode) ──
    if req.mode == Mode.PLAN:
        gs.agent.context["plan_mode"] = True
    else:
        gs.agent.context.pop("plan_mode", None)
    try:
        from agent_gateway import db
        db.save_session_mode(sid, (req.mode.value if req.mode else None) or "agent.fast")
    except Exception:
        pass
    ok_flag = gs.post_message(text)
    debug("chat.send posted sid=%r seq_now=%r ok=%s",
          sid, getattr(gs.agent, "_seq", 0), ok_flag)
    if not ok_flag:
        return AgentResponse(req.request_id, ok=False,
                             error="a turn is already in flight")
    return AgentResponse(req.request_id, payload={"ok": True})


@handler(ReqMethod.CHAT_INTERRUPT)
async def chat_interrupt(req, ctx: HandlerContext):
    sessions = ctx.sessions
    sid = req.session_id
    if not sid:
        return AgentResponse(req.request_id, ok=False, error="missing session_id")
    gs = sessions.get(sid)
    if gs is None:
        debug("chat.interrupt sid=%r NOT FOUND", sid)
        return AgentResponse(req.request_id, ok=False, error="session not found")
    intent = req.params.get("intent", "cancel")
    debug("chat.interrupt sid=%r intent=%r", sid, intent)
    if intent == "pause":
        gs.interrupt()
    elif intent == "supplement":
        new_input = req.params.get("new_input") or ""
        if new_input:
            gs.post_message(new_input)
    else:
        gs.interrupt()
    return AgentResponse(req.request_id, payload={"ok": True, "intent": intent})


@handler(ReqMethod.CHAT_USER_ANSWER)
async def chat_user_answer(req, ctx: HandlerContext):
    sessions = ctx.sessions
    sid = req.session_id
    if not sid:
        return AgentResponse(req.request_id, ok=False, error="missing session_id")
    gs = sessions.get(sid)
    if gs is None:
        return AgentResponse(req.request_id, ok=False, error="session not found")
    rid = req.params.get("request_id", "")
    answers = req.params.get("answers") or []
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
