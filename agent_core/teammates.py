"""agent_core.teammates — extracted from code.py (s20 comprehensive agent)."""
from pathlib import Path
import json
import re
import threading
import time
from agent_core import adapter
from agent_core.blocks import has_tool_use, extract_text
from agent_core.bus import BUS, ProtocolState, new_request_id, pending_requests
from agent_core.env import MODEL, session_dir, set_session_dir
from agent_core.tasks import _tasks_dir, can_start, claim_task, complete_task, list_tasks, load_task
from agent_core.worktrees import _worktrees_dir
# call_tool_handler / run_bash / run_read / run_write imported lazily inside
# spawn_teammate_thread to avoid a tools<->teammates circular import (tools
# top-imports teammates to build BUILTIN_HANDLERS at load time).


active_teammates: dict[str, bool] = {}

# team_name → leader teammate name. Set by start_team so the main loop's
# send_to_leader tool can address the leader without knowing its name.
_team_leaders: dict[str, str] = {}

IDLE_POLL_INTERVAL = 5

IDLE_TIMEOUT = 60

def scan_unclaimed_tasks() -> list[dict]:
    unclaimed = []
    for f in sorted(_tasks_dir().glob("task_*.json")):
        task = json.loads(f.read_text())
        if (task.get("status") == "pending"
                and not task.get("owner")
                and can_start(task["id"])):
            unclaimed.append(task)
    return unclaimed

def idle_poll(agent_name: str, messages: list,
              name: str, role: str,
              worktree_context: dict | None = None,
              overseer: str = "lead") -> str:
    # Autonomous teammates wake up for inbox messages first, then look for
    # unclaimed tasks. This keeps direct protocol messages higher priority.
    for _ in range(IDLE_TIMEOUT // IDLE_POLL_INTERVAL):
        time.sleep(IDLE_POLL_INTERVAL)
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

def spawn_teammate_thread(name: str, role: str, prompt: str, *,
                          agent_key: str | None = None,
                          worktree: str | None = None,
                          model: str | None = None,
                          tool_names: list[str] | None = None,
                          persona: str | None = None,
                          display_name: str | None = None,
                          overseer: str = "lead") -> str:
    """Spawn an autonomous teammate thread.

    overseer = who this teammate reports to / submits plans to. For a team
    member, overseer = the leader's name; for a team leader, overseer = "lead"
    (the main loop). Result messages, shutdown responses, and submit_plan
    requests are all routed to overseer."""
    from agent_core.tools import (call_tool_handler, run_bash, run_read,
                                  run_write, run_edit, run_glob, run_list_dir,
                                  teammate_tool_schemas,
                                  MEMBER_TOOL_NAMES, SUBMIT_PLAN_TOOL)
    if name in active_teammates:
        return f"Teammate '{name}' already exists"

    member_model = model or MODEL

    # session_dir() is threading.local and child threads don't inherit it —
    # capture it here and restore inside run() so the teammate's mailbox
    # reads/writes, task graph, and worktrees resolve to the lead's session.
    # Without this, send_message (lead) writes to .sessions/<sid>/.mailboxes/
    # but the teammate reads from the default workspace .mailboxes/ — messages
    # never arrive and replies never reach the lead.
    captured_session_dir = session_dir()

    # Plan approval is a real gate: after submit_plan, the teammate stops
    # taking model/tool steps until lead sends plan_approval_response.
    protocol_ctx = {"waiting_plan": None}
    system = f"You are '{name}'"
    if display_name:
        system += f", {display_name}"
    system += f", a {role}. Use tools to complete tasks."
    if persona:
        system += f"\nPersona: {persona}"
    if worktree:
        system += f"\nWork in your isolated worktree: {worktree}"
    else:
        system += " If a task has a worktree, work in that directory."

    def handle_inbox_message(name: str, msg: dict, messages: list):
        msg_type = msg.get("type", "message")
        meta = msg.get("metadata", {})
        req_id = meta.get("request_id", "")
        if msg_type == "shutdown_request":
            BUS.send(name, overseer, "Shutting down.",
                     "shutdown_response",
                     {"request_id": req_id, "approve": True})
            return True
        if msg_type == "plan_approval_response":
            approve = meta.get("approve", False)
            if req_id == protocol_ctx["waiting_plan"]:
                protocol_ctx["waiting_plan"] = None
            messages.append({"role": "user",
                "content": "[Plan approved]" if approve
                           else f"[Plan rejected] {msg['content']}"})
        return False

    def run():
        # Restore the lead's session_dir in this child thread so mailbox/task/
        # worktree paths match the lead's session (threading.local isn't inherited).
        set_session_dir(captured_session_dir)

        # Bound upfront when a member worktree is given (team isolation from
        # turn 1); otherwise stays None until a task with a worktree is claimed.
        wt_ctx = {"path": worktree}

        def _wt_cwd():
            # Once a task with a worktree is claimed, all teammate file tools
            # transparently run inside that isolated directory.
            p = wt_ctx["path"]
            return Path(p) if p else None

        def _wrap_file(handler):
            # Redirect a file tool into the member's worktree. cwd is popped
            # because the teammate schemas never declare it.
            def wrapped(**kwargs):
                kwargs.pop("cwd", None)
                return handler(cwd=_wt_cwd(), **kwargs)
            return wrapped

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
                # Only redirect into a task worktree if the teammate doesn't
                # already have its own member worktree bound.
                task = load_task(task_id)
                wt_ctx["path"] = (str(_worktrees_dir() / task.worktree)
                                  if task.worktree else None)
            return result

        def _run_complete_task(task_id: str):
            result = complete_task(task_id)
            if not worktree:
                wt_ctx["path"] = None
            return result

        # ── Stateful protocol handlers (closures over name + overseer) ──
        # send_message: deliver to any peer (member→leader, leader→member,
        #   or leader→"lead" to report to the main loop).
        # request_plan / request_shutdown / review_plan: leader-side coordination
        #   over members. review_plan reuses the shared pending_requests global
        #   so approvals route by request_id to state.sender (the submitter).
        def _send_message(to: str, content: str) -> str:
            BUS.send(name, to, content)
            return f"Sent to {to}"

        def _request_plan(teammate: str, task: str) -> str:
            BUS.send(name, teammate, f"Submit plan for: {task}", "message")
            return f"Asked {teammate} to submit a plan"

        def _request_shutdown(teammate: str) -> str:
            req_id = new_request_id()
            pending_requests[req_id] = ProtocolState(
                request_id=req_id, type="shutdown",
                sender=name, target=teammate,
                status="pending", payload="")
            BUS.send(name, teammate, "Shut down.", "shutdown_request",
                     {"request_id": req_id})
            return f"Shutdown request sent to {teammate}"

        def _review_plan(request_id: str, approve: bool,
                         feedback: str = "") -> str:
            state = pending_requests.get(request_id)
            if not state:
                return f"Request {request_id} not found"
            state.status = "approved" if approve else "rejected"
            BUS.send(name, state.sender,
                     feedback or ("Approved" if approve else "Rejected"),
                     "plan_approval_response",
                     {"request_id": request_id, "approve": approve})
            return f"Plan {'approved' if approve else 'rejected'}"

        stateful_handlers = {
            "send_message": _send_message,
            "list_tasks": _run_list_tasks,
            "claim_task": _run_claim_task,
            "complete_task": _run_complete_task,
            "request_plan": _request_plan,
            "request_shutdown": _request_shutdown,
            "review_plan": _review_plan,
        }

        messages = [{"role": "user", "content": prompt}]
        sent_reply = False  # whether we've already streamed a text reply inline

        # Effective toolset: the agent def's tool_names (a leader gets
        # LEADER_TOOL_NAMES, a member gets MEMBER_TOOL_NAMES), defaulting to
        # MEMBER_TOOL_NAMES for ad-hoc spawns. Schemas resolve through
        # teammate_tool_schemas (covers BUILTIN_TOOLS + the submit_plan /
        # request_plan / request_shutdown literals).
        effective_names = list(tool_names) if tool_names else list(MEMBER_TOOL_NAMES)
        sub_tools = teammate_tool_schemas(effective_names)
        # submit_plan is always available (special-cased in the loop below);
        # ensure its schema is present even if the caller omitted it.
        if not any(t["name"] == "submit_plan" for t in sub_tools):
            sub_tools.append(SUBMIT_PLAN_TOOL)

        # Handlers: stateful protocol handler → file-wrapped → resolved builtin.
        from agent_core.subagent import _resolve_toolset
        try:
            _, resolved_handlers = _resolve_toolset(
                [n for n in effective_names if n not in stateful_handlers])
        except KeyError:
            resolved_handlers = {}
        file_handler_map = {"bash": run_bash, "read_file": run_read,
                            "write_file": run_write, "edit_file": run_edit,
                            "glob": run_glob, "list_dir": run_list_dir}
        sub_handlers = {}
        for t in sub_tools:
            tn = t["name"]
            if tn in stateful_handlers:
                sub_handlers[tn] = stateful_handlers[tn]
            elif tn in file_handler_map:
                sub_handlers[tn] = _wrap_file(file_handler_map[tn])
            elif tn in resolved_handlers:
                sub_handlers[tn] = resolved_handlers[tn]

        while True:
            if len(messages) <= 3:
                messages.insert(0, {"role": "user",
                    "content": f"<identity>You are '{name}', role: {role}. "
                               f"Continue your work.</identity>"})
            should_shutdown = False
            for _ in range(10):
                inbox = BUS.read_inbox(name)
                for msg in inbox:
                    stopped = handle_inbox_message(name, msg, messages)
                    if stopped:
                        should_shutdown = True
                        break
                if should_shutdown:
                    break
                if protocol_ctx["waiting_plan"]:
                    # Poll only for protocol replies while the approval gate is
                    # closed; do not let the model continue with the task.
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
                        model=member_model, system=system, messages=messages[-20:],
                        tools=sub_tools, max_tokens=8000)
                except Exception as _e:
                    # Surface LLM failures instead of dying silently — otherwise a
                    # bad member model makes the teammate vanish with no diagnostics.
                    print(f"  \033[31m[teammate {name}] chat_create failed "
                          f"(model={member_model}): {type(_e).__name__}: {_e}\033[0m")
                    break
                messages.append({"role": "assistant", "content": response.content})
                if not has_tool_use(response.content):
                    # Send the text reply to the lead immediately instead of
                    # only at thread exit — otherwise the lead waits the full
                    # idle_poll timeout (60s) before check_inbox sees anything.
                    _reply = extract_text(response.content)
                    if _reply:
                        BUS.send(name, overseer, _reply, "result")
                        sent_reply = True
                    break
                results = []
                for block in response.content:
                    if block.type == "tool_use":
                        if block.name == "submit_plan":
                            output = _teammate_submit_plan(
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
                                # Never let a tool-handler exception kill the
                                # teammate thread — return an error string the
                                # model can react to (e.g. complete_task on a
                                # non-existent task_id raising FileNotFoundError).
                                output = (f"Error: {block.name} raised "
                                          f"{type(_he).__name__}: {_he}")
                                print(f"  \033[31m[teammate {name}] {block.name} "
                                      f"raised {type(_he).__name__}: {_he}\033[0m")
                        results.append({"type": "tool_result",
                                        "tool_use_id": block.id,
                                        "content": str(output)})
                        if protocol_ctx["waiting_plan"]:
                            # Ignore later tool_use blocks from the same model
                            # response; they belong after approval, not before.
                            break
                messages.append({"role": "user", "content": results})
                if protocol_ctx["waiting_plan"]:
                    break
            if should_shutdown:
                break
            if protocol_ctx["waiting_plan"]:
                continue
            idle_result = idle_poll(name, messages, name, role, wt_ctx,
                                    overseer=overseer)
            if idle_result in ("shutdown", "timeout"):
                break

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
        # Only send a final result at exit if we never streamed one inline
        # (avoids duplicating the reply we already sent on the no-tool-use turn).
        if not sent_reply:
            BUS.send(name, overseer, summary, "result")
        active_teammates.pop(name, None)

    active_teammates[name] = True
    threading.Thread(target=run, daemon=True).start()
    return f"Teammate '{name}' spawned as {role}"

def _teammate_submit_plan(from_name: str, plan: str,
                          overseer: str = "lead") -> str:
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id, type="plan_approval",
        sender=from_name, target=overseer,
        status="pending", payload=plan)
    BUS.send(from_name, overseer, plan,
             "plan_approval_request",
             {"request_id": req_id})
    return f"Plan submitted ({req_id})"

def run_request_shutdown(teammate: str) -> str:
    req_id = new_request_id()
    pending_requests[req_id] = ProtocolState(
        request_id=req_id, type="shutdown",
        sender="lead", target=teammate,
        status="pending", payload="")
    BUS.send("lead", teammate, "Shut down.", "shutdown_request",
             {"request_id": req_id})
    return f"Shutdown request sent to {teammate}"

def run_request_plan(teammate: str, task: str) -> str:
    BUS.send("lead", teammate, f"Submit plan for: {task}", "message")
    return f"Asked {teammate} to submit a plan"

def run_review_plan(request_id: str, approve: bool,
                    feedback: str = "") -> str:
    state = pending_requests.get(request_id)
    if not state:
        return f"Request {request_id} not found"
    state.status = "approved" if approve else "rejected"
    BUS.send("lead", state.sender,
             feedback or ("Approved" if approve else "Rejected"),
             "plan_approval_response",
             {"request_id": request_id, "approve": approve})
    return f"Plan {'approved' if approve else 'rejected'}"


def run_team_info(team_name: str) -> str:
    """Return a team's static config + live runtime state for the main loop.

    Combines the saved entry from agents_config.json (leader/members config,
    lifecycle, spawn mode, …) with runtime state from _team_leaders /
    active_teammates (which leader is registered, which teammates are alive).
    Used by the main-loop `team_info` tool so the lead can inspect a team it
    started (or a saved team it hasn't) without messaging anyone."""
    from agent_core.agents import get_team, list_team_names

    team = get_team(team_name)
    if team is None:
        avail = list_team_names()
        return (f"No team named {team_name!r}. "
                f"Available: {avail if avail else '(none)'}")

    leader_name = _team_leaders.get(team_name)
    lines = [f"Team: {team_name}"]
    if leader_name:
        lines.append(f"Leader (started): {leader_name} "
                     f"[active={bool(active_teammates.get(leader_name))}]")
    else:
        lines.append("Leader: (team not started)")

    leader_def = team.get("leader") or {}
    if leader_def:
        lines.append(
            f"  leader config: member_name={leader_def.get('member_name','')}, "
            f"display_name={leader_def.get('display_name','')}, "
            f"agent_key={leader_def.get('agent_key','')}")
        if leader_def.get("persona"):
            lines.append(f"  leader persona: {leader_def['persona']}")

    members = team.get("predefined_members") or []
    lines.append(f"Members ({len(members)}):")
    for m in members:
        if not isinstance(m, dict):
            continue
        mname = m.get("member_name", "")
        active = bool(active_teammates.get(mname))
        lines.append(
            f"  - {mname} [active={active}] "
            f"agent_key={m.get('agent_key','')} "
            f"display_name={m.get('display_name','')}")

    tmate = team.get("teammate") or {}
    if isinstance(tmate, dict) and tmate.get("agent_key"):
        lines.append(f"teammate template agent_key: {tmate['agent_key']}")

    for k in ("lifecycle", "teammate_mode", "spawn_mode",
              "enable_permissions"):
        v = team.get(k)
        if v is not None and v != "":
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


def start_team(team_name: str, task: str = "") -> str:
    """Launch a saved team (from .agents/agents_config.json) in 3-tier mode:
    main loop → team leader → members. Spawns a dedicated LEADER teammate (from
    team.leader.agent_key) plus the predefined members, each in its own git
    worktree under .worktrees/. The leader coordinates members and reports to
    the main loop; members report to the leader. Worktree creation is best-effort.
    Returns a coordination summary naming the leader."""
    from agent_core.agents import get_team, list_team_names, get_agent
    from agent_core.worktrees import create_worktree, _worktrees_dir
    from agent_core.tools import LEADER_TOOL_NAMES, MEMBER_TOOL_NAMES

    team = get_team(team_name)
    if team is None:
        avail = list_team_names()
        return (f"No team named {team_name!r}. "
                f"Available: {avail if avail else '(none)'}")

    leader_def = team.get("leader") or {}
    leader_name = (leader_def.get("member_name")
                   or f"{team_name}-leader")
    leader_akey = leader_def.get("agent_key") or ""
    leader_dname = leader_def.get("display_name") or leader_name
    leader_persona = leader_def.get("persona") or ""

    # Collect members. predefined_members is the explicit roster; if none and a
    # teammate template agent_key is set, spawn one dynamic worker from it.
    members = []
    for m in team.get("predefined_members") or []:
        if isinstance(m, dict) and m.get("member_name"):
            members.append(m)
    if not members:
        tmate = team.get("teammate") or {}
        ak = tmate.get("agent_key") if isinstance(tmate, dict) else None
        if ak:
            members = [{"member_name": f"{team_name}-worker",
                        "display_name": "", "persona": "",
                        "prompt_hint": "", "agent_key": ak}]
    if not members:
        return f"Team {team_name!r} has no members to spawn."

    # Register the leader so the main loop's send_to_leader tool can address it.
    _team_leaders[team_name] = leader_name

    # ── Spawn members first (so the leader's roster is real when it starts) ──
    member_roster = []
    for m in members:
        mname = m.get("member_name")
        dname = m.get("display_name") or ""
        persona = m.get("persona") or ""
        prompt_hint = m.get("prompt_hint") or ""
        akey = m.get("agent_key") or ""

        agent_model = None
        if akey:
            defn = get_agent(akey)
            if defn is not None:
                agent_model = defn.get("model") or None

        # Per-member worktree for isolation (best-effort).
        wt_name = f"{team_name}-{mname}"
        wt_path = None
        try:
            res = create_worktree(wt_name)
        except Exception:
            res = ""
        if isinstance(res, str) and res.startswith("Worktree '"):
            wt_path = str(_worktrees_dir() / wt_name)

        parts = [f"Team: {team_name}."]
        if task:
            parts.append(f"Task: {task}")
        parts.append(f"Report to your leader '{leader_name}' via send_message; "
                     f"submit plans via submit_plan.")
        if persona:
            parts.append(f"Persona: {persona}")
        if prompt_hint:
            parts.append(f"Hint: {prompt_hint}")
        member_prompt = "\n".join(parts)

        spawn_teammate_thread(
            name=mname, role=dname or mname, prompt=member_prompt,
            agent_key=akey or None, worktree=wt_path,
            model=agent_model, tool_names=MEMBER_TOOL_NAMES,
            persona=persona or None, display_name=dname or None,
            overseer=leader_name,
        )
        member_roster.append((mname, dname or mname, wt_path))

    # ── Spawn the leader ──
    leader_model = None
    if leader_akey:
        ldefn = get_agent(leader_akey)
        if ldefn is not None:
            leader_model = ldefn.get("model") or None

    wt_name = f"{team_name}-{leader_name}"
    leader_wt = None
    try:
        res = create_worktree(wt_name)
    except Exception:
        res = ""
    if isinstance(res, str) and res.startswith("Worktree '"):
        leader_wt = str(_worktrees_dir() / wt_name)

    roster_lines = [f"  - {mn} [{dn}]" for mn, dn, _ in member_roster]
    leader_parts = [f"Team: {team_name}. You are the team LEADER '{leader_name}'."]
    if task:
        leader_parts.append(f"Task: {task}")
    leader_parts.append(
        "Coordinate your members via send_message / request_plan / review_plan / "
        "request_shutdown. When you have an overall plan, submit it via submit_plan "
        "for the lead to approve (验收). Report final results to the lead via "
        f"send_message(to=\"lead\", ...). Members: \n" + "\n".join(roster_lines))
    if leader_persona:
        leader_parts.append(f"Persona: {leader_persona}")
    leader_prompt = "\n".join(leader_parts)

    spawn_teammate_thread(
        name=leader_name, role=leader_dname, prompt=leader_prompt,
        agent_key=leader_akey or None, worktree=leader_wt,
        model=leader_model, tool_names=LEADER_TOOL_NAMES,
        persona=leader_persona or None, display_name=leader_dname or None,
        overseer="lead",
    )

    summary_lines = [f"Team {team_name!r} launched (3-tier)."]
    summary_lines.append(f"Leader: {leader_name} [{leader_dname}] @ "
                         f"{leader_wt or '(shared workspace)'}")
    summary_lines.append("Members:")
    for mn, dn, wt in member_roster:
        summary_lines.append(f"  - {mn} [{dn}] @ {wt or '(shared workspace)'}")
    summary_lines.append("")
    summary_lines.append(
        "You drive the team via: send_to_leader(team_name, content), "
        "check_inbox, review_plan(request_id, approve, feedback). "
        "The leader coordinates members; you cannot message members directly.")
    return "\n".join(summary_lines)
