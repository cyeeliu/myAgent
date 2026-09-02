"""Teammate agent loop — extracted from ``teammates.py``.

Contains the standalone ``run_teammate_loop`` function (previously a
290-line closure inside ``spawn_teammate_thread``), plus the idle
polling, inbox handling, and tool-handler factory functions that it
depends on. All dependencies are passed as parameters — no closures.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

from agent_core import adapter
from agent_core.blocks import has_tool_use, extract_text
from agent_core.bus import BUS
from agent_core.env import MODEL, session_dir, set_session_dir
from agent_core.tasks import (
    _tasks_dir, can_start, claim_task, complete_task, list_tasks, load_task,
)
from agent_core.worktrees import _worktrees_dir
from agent_core.team_events import (
    TeammateEventShim, emit_team_event, now_ms,
    TEAM_MEMBER_SPAWNED, TEAM_MEMBER_DONE, TEAM_MEMBER_SHUTDOWN,
    TEAM_TASK_COMPLETED,
)
from agent_core.team_protocol import (
    submit_plan as protocol_submit_plan,
    request_plan as protocol_request_plan,
    request_shutdown as protocol_request_shutdown,
    review_plan as protocol_review_plan,
)

IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60


# ── Idle polling ──

def scan_unclaimed_tasks() -> list[dict]:
    """Return all pending, unowned, startable tasks."""
    unclaimed = []
    for f in sorted(_tasks_dir().glob("task_*.json")):
        task = json.loads(f.read_text())
        if (task.get("status") == "pending"
                and not task.get("owner")
                and can_start(task["id"])):
            unclaimed.append(task)
    return unclaimed


def idle_poll(
    agent_name: str,
    messages: list,
    name: str,
    role: str,
    worktree_context: dict | None = None,
    overseer: str = "boss",
    boss_session: Any = None,
) -> str:
    """Poll for inbox messages or unclaimed tasks while idle.

    Returns ``"work"`` if something was found, ``"shutdown"`` if a
    shutdown request was received, ``"interrupted"`` if the boss was
    interrupted, or ``"timeout"`` if nothing arrived within
    ``IDLE_TIMEOUT`` seconds.
    """
    for _ in range(IDLE_TIMEOUT // IDLE_POLL_INTERVAL):
        time.sleep(IDLE_POLL_INTERVAL)
        if boss_session is not None and getattr(boss_session, "interrupted", False):
            return "interrupted"
        inbox = BUS.read_inbox(agent_name)
        if inbox:
            for msg in inbox:
                if msg.get("type") == "shutdown_request":
                    req_id = msg.get("metadata", {}).get("request_id", "")
                    BUS.send(name, overseer, "Shutting down.",
                             "shutdown_response",
                             {"request_id": req_id, "approve": True})
                    return "shutdown"
            messages.append({"role": "user",
                "content": "<inbox>" + json.dumps(inbox) + "</inbox>"})
            return "work"
        unclaimed = scan_unclaimed_tasks()
        if unclaimed:
            task_data = unclaimed[0]
            result = claim_task(task_data["id"], agent_name)
            if "Claimed" in result:
                wt_info = ""
                if task_data.get("worktree"):
                    wt_path = _worktrees_dir() / task_data["worktree"]
                    wt_info = f"\nWork directory: {wt_path}"
                    if worktree_context is not None:
                        worktree_context["path"] = str(wt_path)
                messages.append({"role": "user",
                    "content": f"<auto-claimed>Task {task_data['id']}: "
                               f"{task_data['subject']}{wt_info}</auto-claimed>"})
                return "work"
    return "timeout"


# ── Inbox handling ──

def handle_inbox_message(
    name: str,
    msg: dict,
    messages: list,
    protocol_ctx: dict,
    overseer: str,
    boss_session: Any,
) -> bool:
    """Process one inbox message. Returns ``True`` if the teammate
    should shut down, ``False`` otherwise."""
    msg_type = msg.get("type", "message")
    meta = msg.get("metadata", {})
    req_id = meta.get("request_id", "")
    if msg_type == "shutdown_request":
        BUS.send(name, overseer, "Shutting down.",
                 "shutdown_response",
                 {"request_id": req_id, "approve": True})
        emit_team_event(boss_session, "team_member", {
            "type": TEAM_MEMBER_SHUTDOWN,
            "member_id": name,
            "timestamp": now_ms(),
        })
        return True
    if msg_type == "plan_approval_response":
        approve = meta.get("approve", False)
        if req_id == protocol_ctx["waiting_plan"]:
            protocol_ctx["waiting_plan"] = None
        messages.append({"role": "user",
            "content": "[Plan approved]" if approve
                       else f"[Plan rejected] {msg['content']}"})
    return False


# ── Tool handler factory ──

def create_tool_handlers(
    name: str,
    overseer: str,
    wt_ctx: dict,
    protocol_ctx: dict,
) -> dict[str, Any]:
    """Build the stateful tool handlers for a teammate.

    These are the protocol + task handlers that capture the teammate's
    name, overseer, worktree context, and protocol context. File-tool
    wrappers are built separately in ``run_teammate_loop`` because they
    need the resolved handler functions from ``tools.py``.
    """
    def _run_list_tasks():
        tasks = list_tasks()
        if not tasks:
            return "No tasks."
        return "\n".join(
            f"  {t.id}: {t.subject} [{t.status}]"
            + (f" (wt:{t.worktree})" if t.worktree else "")
            for t in tasks)

    def _run_claim_task(task_id: str):
        result = claim_task(task_id, owner=name)
        if "Claimed" in result and not wt_ctx["path"]:
            task = load_task(task_id)
            wt_ctx["path"] = (str(_worktrees_dir() / task.worktree)
                              if task.worktree else None)
        return result

    def _run_complete_task(task_id: str):
        result = complete_task(task_id)
        if not wt_ctx["path"]:
            wt_ctx["path"] = None
        return result

    def _send_message(to: str, content: str) -> str:
        BUS.send(name, to, content)
        return f"Sent to {to}"

    def _request_plan(teammate: str, task: str) -> str:
        return protocol_request_plan(name, teammate, task)

    def _request_shutdown(teammate: str) -> str:
        return protocol_request_shutdown(name, teammate)

    def _review_plan(request_id: str, approve: bool,
                     feedback: str = "") -> str:
        return protocol_review_plan(
            name, request_id, approve, feedback,
            self_plan_guard=True,
        )

    return {
        "send_message": _send_message,
        "list_tasks": _run_list_tasks,
        "claim_task": _run_claim_task,
        "complete_task": _run_complete_task,
        "request_plan": _request_plan,
        "request_shutdown": _request_shutdown,
        "review_plan": _review_plan,
    }


# ── Teammate loop ──

def run_teammate_loop(
    *,
    name: str,
    role: str,
    prompt: str,
    display_name: str | None,
    persona: str | None,
    worktree: str | None,
    overseer: str,
    boss_session: Any,
    team_name: str | None,
    member_mode: str,
    member_model: str | None,
    system: str,
    captured_session_dir: Any,
    cur_sd: str,
    tool_names: list[str] | None,
    agent_key: str | None,
    on_exit: Callable[[], None] | None = None,
) -> None:
    """Run the teammate agent loop in the current thread.

    This is the body of the teammate thread, previously a 290-line
    closure inside ``spawn_teammate_thread``. All dependencies are
    passed as keyword arguments — no closure capture.

    The caller is responsible for registering the teammate in
    ``team_state.registry`` before calling this function, and this
    function handles unregistration on exit.

    ``on_exit`` is an optional callback invoked in the ``finally``
    block after cleanup — used by ``start_team`` to unregister bus
    taps when the leader thread exits.
    """
    from agent_core.tools import (
        call_tool_handler, run_bash, run_read, run_write,
        run_edit, run_glob, run_list_dir,
        teammate_tool_schemas,
        MEMBER_TOOL_NAMES, SUBMIT_PLAN_TOOL,
    )
    from agent_core.subagent import _resolve_toolset
    from agent_core.team_state import registry

    model = member_model or MODEL

    # Restore the boss's session_dir in this child thread.
    set_session_dir(captured_session_dir)

    # Surface this teammate to the frontend TeamArea immediately.
    emit_team_event(boss_session, "team_member", {
        "type": TEAM_MEMBER_SPAWNED,
        "member_id": name,
        "name": display_name or name,
        "status": "running",
        "timestamp": now_ms(),
        "mode": member_mode,
    })

    # Worktree context — bound upfront when a member worktree is given;
    # otherwise stays None until a task with a worktree is claimed.
    wt_ctx: dict = {"path": worktree}
    protocol_ctx: dict = {"waiting_plan": None}

    # ── File tool wrapper ──
    def _wt_cwd():
        p = wt_ctx["path"]
        return Path(p) if p else None

    def _wrap_file(handler):
        def wrapped(**kwargs):
            kwargs.pop("cwd", None)
            return handler(cwd=_wt_cwd(), **kwargs)
        return wrapped

    # ── Tool resolution ──
    stateful_handlers = create_tool_handlers(name, overseer, wt_ctx, protocol_ctx)

    messages = [{"role": "user", "content": prompt}]
    sent_reply = False

    effective_names = list(tool_names) if tool_names else list(MEMBER_TOOL_NAMES)
    sub_tools = teammate_tool_schemas(effective_names)
    if not any(t["name"] == "submit_plan" for t in sub_tools):
        sub_tools.append(SUBMIT_PLAN_TOOL)

    try:
        _, resolved_handlers = _resolve_toolset(
            [n for n in effective_names if n not in stateful_handlers])
    except KeyError:
        resolved_handlers = {}

    file_handler_map = {
        "bash": run_bash, "read_file": run_read,
        "write_file": run_write, "edit_file": run_edit,
        "glob": run_glob, "list_dir": run_list_dir,
    }
    sub_handlers: dict[str, Any] = {}
    for t in sub_tools:
        tn = t["name"]
        if tn in stateful_handlers:
            sub_handlers[tn] = stateful_handlers[tn]
        elif tn in file_handler_map:
            sub_handlers[tn] = _wrap_file(file_handler_map[tn])
        elif tn in resolved_handlers:
            sub_handlers[tn] = resolved_handlers[tn]

    # ── Main loop ──
    try:
        while True:
            if boss_session is not None and getattr(boss_session, "interrupted", False):
                break
            if len(messages) <= 3:
                messages.insert(0, {"role": "user",
                    "content": f"<identity>You are '{name}', role: {role}. "
                               f"Continue your work.</identity>"})
            should_shutdown = False
            for _ in range(10):
                if boss_session is not None and getattr(boss_session, "interrupted", False):
                    should_shutdown = True
                    break
                inbox = BUS.read_inbox(name)
                for msg in inbox:
                    stopped = handle_inbox_message(
                        name, msg, messages, protocol_ctx, overseer, boss_session)
                    if stopped:
                        should_shutdown = True
                        break
                if should_shutdown:
                    break
                if protocol_ctx["waiting_plan"]:
                    time.sleep(IDLE_POLL_INTERVAL)
                    continue
                if inbox and not should_shutdown:
                    non_protocol = [m for m in inbox
                                    if m.get("type") == "message"]
                    if non_protocol:
                        messages.append({"role": "user",
                            "content": "<inbox>" + json.dumps(non_protocol) + "</inbox>"})
                try:
                    response = adapter.chat_create(
                        model=model, system=system, messages=messages[-20:],
                        tools=sub_tools, max_tokens=8000,
                        stream=True,
                        events=TeammateEventShim(boss_session) if boss_session else None)
                except Exception as _e:
                    logger.error("teammate %s chat_create failed (model=%s): %s: %s",
                                 name, model, type(_e).__name__, _e)
                    break
                if getattr(response, "interrupted", False):
                    should_shutdown = True
                    break
                messages.append({"role": "assistant", "content": response.content})
                if not has_tool_use(response.content):
                    _reply = extract_text(response.content)
                    if _reply:
                        BUS.send(name, overseer, _reply, "result")
                        sent_reply = True
                    break
                results = []
                for block in response.content:
                    if block.type == "tool_use":
                        if boss_session is not None and getattr(boss_session, "interrupted", False):
                            should_shutdown = True
                            break
                        if block.name == "submit_plan":
                            output = protocol_submit_plan(
                                name, block.input.get("plan", ""), overseer)
                            match = re.search(r"\((req_\d+)\)", output)
                            protocol_ctx["waiting_plan"] = (
                                match.group(1) if match else output)
                        else:
                            handler = sub_handlers.get(block.name)
                            try:
                                output = call_tool_handler(handler, block.input,
                                                           block.name)
                            except Exception as _he:
                                output = (f"Error: {block.name} raised "
                                          f"{type(_he).__name__}: {_he}")
                                logger.error("teammate %s %s raised %s: %s",
                                             name, block.name,
                                             type(_he).__name__, _he)
                        results.append({"type": "tool_result",
                                        "tool_use_id": block.id,
                                        "content": str(output)})
                        if protocol_ctx["waiting_plan"]:
                            break
                messages.append({"role": "user", "content": results})
                if protocol_ctx["waiting_plan"]:
                    break
            if should_shutdown:
                break
            if protocol_ctx["waiting_plan"]:
                continue
            if boss_session is not None and getattr(boss_session, "interrupted", False):
                break
            idle_result = idle_poll(name, messages, name, role, wt_ctx,
                                    overseer=overseer,
                                    boss_session=boss_session)
            if idle_result in ("shutdown", "timeout", "interrupted"):
                break
    except Exception:
        logger.exception("Teammate %s crashed unexpectedly", name)
    finally:
        # ── Cleanup (guaranteed even on crash) ──
        try:
            summary = "Done."
            for msg in reversed(messages):
                if msg["role"] == "assistant" and isinstance(msg["content"], list):
                    for b in msg["content"]:
                        if getattr(b, "type", None) == "text":
                            summary = b.text
                            break
                    else:
                        continue
                    break
            if not sent_reply:
                BUS.send(name, overseer, summary, "result")

            # Only pop our own entry — a newer session may have evicted us.
            registry.unregister_teammate(name, cur_sd)

            emit_team_event(boss_session, "team_member", {
                "type": TEAM_MEMBER_DONE,
                "member_id": name,
                "new_status": "done",
                "timestamp": now_ms(),
            })
            if member_mode == "leader" and team_name:
                emit_team_event(boss_session, "team_task", {
                    "type": TEAM_TASK_COMPLETED,
                    "task_id": f"{team_name}-root",
                    "team_name": team_name,
                    "status": "completed",
                    "timestamp": now_ms(),
                })
        except Exception:
            logger.exception("Teammate %s cleanup failed", name)

        # Team-level cleanup callback (e.g. bus tap unregistration).
        if on_exit is not None:
            try:
                on_exit()
            except Exception:
                logger.exception("Teammate %s on_exit callback failed", name)
