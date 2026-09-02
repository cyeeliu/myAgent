"""Prompt builders for team members and leaders.

Extracts the inline string-concatenation prompts from ``start_team``
and ``spawn_teammate_thread`` into reusable, testable pure functions.
"""
from __future__ import annotations

from agent_core.team_types import MemberDef, LeaderDef, TeamConfig


# ── System prompt for the teammate's LLM ──

def build_teammate_system_prompt(
    name: str,
    role: str,
    *,
    display_name: str = "",
    persona: str = "",
    worktree: str | None = None,
) -> str:
    """Build the system prompt for a teammate's ``chat_create`` calls."""
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
    return system


# ── Member initial prompt ──

def build_member_prompt(
    team_name: str,
    task: str,
    leader_name: str,
    member: MemberDef,
) -> str:
    """Build the initial user-message prompt for a team member."""
    parts = [f"Team: {team_name}."]
    if task:
        parts.append(f"Task: {task}")
    parts.append(
        f"Report to your leader '{leader_name}' via send_message; "
        f"submit plans via submit_plan."
    )
    parts.append(
        "send_message is asynchronous — it returns 'Sent' immediately, not a "
        "reply. After sending a message or submit_plan to your leader, END YOUR "
        "TURN; your idle_poll waits for the leader's reply and wakes you on the "
        "next turn. Do not assume silence means the leader is offline."
    )
    if member.persona:
        parts.append(f"Persona: {member.persona}")
    if member.prompt_hint:
        parts.append(f"Hint: {member.prompt_hint}")
    return "\n".join(parts)


# ── Leader coordination instructions ──

def build_coordination_instructions() -> str:
    """The async messaging discipline block for the leader prompt."""
    return (
        "CRITICAL — async messaging discipline (A2A event-driven):\n"
        "- send_message is ASYNCHRONOUS: it returns 'Sent to <name>' immediately, "
        "NOT the recipient's reply. The reply arrives in your inbox on a LATER turn.\n"
        "- After send_message / request_plan / request_shutdown to a member, END "
        "YOUR TURN (produce no further tool_use — a short text note is fine). Your "
        "idle_poll will wait for their reply and wake you with an <inbox> message "
        "on the next turn. THEN react to it.\n"
        "- NEVER conclude 'no response', 'offline', or 'unreachable' from the "
        "send_message tool_result alone — that only means it was delivered, not "
        "that the member saw it. A member is unreachable only after you have ended "
        "your turn and idle_poll timed out (60s) with no reply.\n"
        "- Do NOT send a report to the boss about a member's status in the same "
        "turn you first message that member — wait for the reply first.\n"
        "- Task IDs: call create_task to mint an ID and use the returned ID; NEVER "
        "invent IDs like 'DEBUG-001'. complete_task only accepts IDs from create_task.\n"
        "- review_plan approves a MEMBER's submitted plan (pass the request_id the "
        "member's submit_plan returned). Never review_plan your own plan — the boss "
        "reviews yours."
    )


# ── Leader initial prompt ──

def build_leader_prompt(
    team_name: str,
    task: str,
    leader_name: str,
    leader_display_name: str,
    member_roster: list[tuple[str, str]],
    persona: str = "",
) -> str:
    """Build the initial user-message prompt for the team leader.

    ``member_roster`` is a list of ``(member_name, display_name)``
    tuples for the spawned members.
    """
    roster_lines = [f"  - {mn} [{dn}]" for mn, dn in member_roster]
    parts = [f"Team: {team_name}. You are the team LEADER '{leader_name}'."]
    if task:
        parts.append(f"Task: {task}")
    parts.append(
        "Coordinate your members via send_message / request_plan / review_plan / "
        "request_shutdown. When you have an overall plan, submit it via submit_plan "
        "for the boss to approve (验收). Report final results to the boss via "
        f"send_message(to=\"boss\", ...). Members: \n" + "\n".join(roster_lines))
    parts.append(build_coordination_instructions())
    if persona:
        parts.append(f"Persona: {persona}")
    return "\n".join(parts)


# ── Boss coordination summary (returned by start_team) ──

def build_team_summary(
    team_name: str,
    leader_name: str,
    leader_display_name: str,
    leader_wt: str | None,
    member_roster: list[tuple[str, str, str | None]],
) -> str:
    """Build the coordination summary string returned by ``start_team``
    to the main loop.

    ``member_roster`` is a list of ``(member_name, display_name,
    worktree_path_or_None)`` tuples.
    """
    lines = [f"Team {team_name!r} launched (3-tier)."]
    lines.append(f"Leader: {leader_name} [{leader_display_name}] @ "
                 f"{leader_wt or '(shared workspace)'}")
    lines.append("Members:")
    for mn, dn, wt in member_roster:
        lines.append(f"  - {mn} [{dn}] @ {wt or '(shared workspace)'}")
    lines.append("")
    lines.append(
        "You drive the team via: send_to_leader(team_name, content), "
        "check_inbox, review_plan(request_id, approve, feedback). "
        "The leader coordinates members; you cannot message members directly.")
    lines.append(
        "A2A EVENT-DRIVEN COORDINATION (do NOT use the wait tool for team):\n"
        "- After start_team and after each send_to_leader / review_plan, END YOUR "
        "TURN. The team works asynchronously in background threads.\n"
        "- When a teammate sends a result or status update to you, the system "
        "AUTOMATICALLY re-invokes you with a fresh turn — you don't need to poll "
        "or wait. The teammate's message appears in your context as "
        "<team_messages> at the start of the new turn.\n"
        "- On each re-invocation: call check_inbox to drain any accumulated "
        "messages, process them, then either:\n"
        "  • Send new instructions to the leader and END YOUR TURN again.\n"
        "  • If you have the FINAL result (the leader reports the task done and "
        "    shuts down), summarize for the user and stop.\n"
        "- NEVER call wait(sources=[\"team\",...]) — it blocks your turn and "
        "freezes the session. The A2A callback handles re-invocation for you.\n"
        "- The wait tool is still available for background tasks (sources=[\"background\"]) "
        "if needed, but NOT for team coordination.")
    lines.append(
        "DEGRADATION: A watchdog monitors the team. If all members exit without "
        "sending a final result, you'll receive a [TEAM COMPLETE] notification. "
        "If the team exceeds the timeout (default 10 min), you'll receive a "
        "[TEAM TIMEOUT] notification with partial results. In either case, "
        "summarize whatever results are available for the user — do NOT hang "
        "or retry indefinitely.")
    return "\n".join(lines)
