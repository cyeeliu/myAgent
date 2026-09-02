"""agent_core.teammates — team collaboration composition layer.

This module is a thin orchestration layer that wires together the
focused team sub-modules:

* ``team_types``      — config parsing and type definitions
* ``team_state``      — thread-safe runtime state registry
* ``team_events``     — event emission and event shim
* ``team_protocol``   — plan approval / shutdown handshake
* ``team_prompts``    — prompt builders for members and leaders
* ``teammate_loop``   — the teammate agent loop (run in a thread)

Public API is preserved for ``code.py`` facade, ``tools.py``, and
``context.py`` backward compatibility.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

from agent_core.bus import BUS
from agent_core.env import session_dir, set_session_dir
from agent_core.team_state import registry
from agent_core.team_events import emit_team_event, now_ms, TEAM_TASK_CREATED
from agent_core.team_protocol import (
    submit_plan as _teammate_submit_plan,
    request_plan as _protocol_request_plan,
    request_shutdown as _protocol_request_shutdown,
    review_plan as _protocol_review_plan,
)
from agent_core.teammate_loop import (
    IDLE_POLL_INTERVAL,
    IDLE_TIMEOUT,
    idle_poll,
    scan_unclaimed_tasks,
    run_teammate_loop,
)
from agent_core.team_prompts import (
    build_teammate_system_prompt,
    build_member_prompt,
    build_leader_prompt,
    build_team_summary,
)
from agent_core.team_types import parse_team_config, resolve_members


# ── Backward-compat proxies ──
# ``context.py`` does ``from agent_core.teammates import active_teammates``
# and uses ``list(active_teammates.keys())``. ``tools.py`` uses
# ``_team_leaders.get(team_name)``. These proxy objects delegate to the
# thread-safe ``team_state.registry`` so external readers see live data.

class _ActiveTeammatesProxy:
    """Dict-like proxy delegating to ``team_state.registry``.
    Supports the subset of dict operations used by external code."""
    def get(self, name: str) -> str | None:
        return registry.get_session_dir(name)
    def keys(self):
        return registry.active_names()
    def __contains__(self, name: str) -> bool:
        return registry.is_active(name)
    def __bool__(self) -> bool:
        return bool(registry.active_names())
    def __repr__(self) -> str:
        return repr(registry.active_dict())


class _TeamLeadersProxy:
    """Dict-like proxy for ``_team_leaders`` delegating to registry."""
    def get(self, team_name: str) -> str | None:
        return registry.get_leader(team_name)
    def __contains__(self, team_name: str) -> bool:
        return registry.get_leader(team_name) is not None
    def __repr__(self) -> str:
        return f"<TeamLeadersProxy>"


active_teammates = _ActiveTeammatesProxy()
_team_leaders = _TeamLeadersProxy()
_team_boss_sessions: dict[str, Any] = {}  # kept for backward compat


# ── Boss-side protocol functions (main loop tools) ──

def run_request_shutdown(teammate: str) -> str:
    """Boss-side tool: request a teammate to shut down."""
    return _protocol_request_shutdown("boss", teammate)


def run_request_plan(teammate: str, task: str) -> str:
    """Boss-side tool: ask a teammate to submit a plan."""
    return _protocol_request_plan("boss", teammate, task)


def run_review_plan(request_id: str, approve: bool,
                    feedback: str = "") -> str:
    """Boss-side tool: review (approve/reject) a submitted plan."""
    return _protocol_review_plan("boss", request_id, approve, feedback)


# ── Teammate spawning ──

def spawn_teammate_thread(name: str, role: str, prompt: str, *,
                          agent_key: str | None = None,
                          worktree: str | None = None,
                          model: str | None = None,
                          tool_names: list[str] | None = None,
                          persona: str | None = None,
                          display_name: str | None = None,
                          overseer: str = "boss",
                          boss_session=None,
                          team_name: str | None = None,
                          member_mode: str = "member",
                          on_exit: Callable[[], None] | None = None) -> str:
    """Spawn an autonomous teammate thread.

    This is a thin wrapper that validates the spawn, registers the
    teammate in the state registry, and starts a daemon thread running
    ``teammate_loop.run_teammate_loop``.

    ``on_exit`` is an optional callback invoked in the teammate loop's
    ``finally`` block — used by ``start_team`` to unregister bus taps
    when the leader thread exits.
    """
    # session_dir() is threading.local — capture here, restore in the
    # child thread so the teammate's mailbox/task/worktree paths resolve
    # to the boss's session.
    captured_session_dir = session_dir()
    cur_sd = str(captured_session_dir)

    if not registry.register_teammate(name, cur_sd):
        return f"Teammate '{name}' already exists"

    system = build_teammate_system_prompt(
        name, role,
        display_name=display_name or "",
        persona=persona or "",
        worktree=worktree,
    )

    def run():
        run_teammate_loop(
            name=name,
            role=role,
            prompt=prompt,
            display_name=display_name,
            persona=persona,
            worktree=worktree,
            overseer=overseer,
            boss_session=boss_session,
            team_name=team_name,
            member_mode=member_mode,
            member_model=model,
            system=system,
            captured_session_dir=captured_session_dir,
            cur_sd=cur_sd,
            tool_names=tool_names,
            agent_key=agent_key,
            on_exit=on_exit,
        )

    threading.Thread(target=run, daemon=True).start()
    return f"Teammate '{name}' spawned as {role}"


# ── Team info ──

def run_team_info(team_name: str) -> str:
    """Return a team's static config + live runtime state for the main loop."""
    from agent_core.agents import get_team, list_team_names

    team = get_team(team_name)
    if team is None:
        avail = list_team_names()
        return (f"No team named {team_name!r}. "
                f"Available: {avail if avail else '(none)'}")

    leader_name = registry.get_leader(team_name)
    lines = [f"Team: {team_name}"]
    if leader_name:
        lines.append(f"Leader (started): {leader_name} "
                     f"[active={registry.is_active(leader_name)}]")
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
        active = registry.is_active(mname)
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


# ── Team startup ──

# ── Team watchdog ──

# Default timeout for team watchdog: 10 minutes. Override with env var
# TEAM_WATCHDOG_TIMEOUT_SECONDS.
import os as _os
_TEAM_WATCHDOG_TIMEOUT = int(_os.environ.get("TEAM_WATCHDOG_TIMEOUT_SECONDS", "600"))
_TEAM_WATCHDOG_INTERVAL = 5  # poll interval (seconds)
_TEAM_STUCK_THRESHOLD = 120  # no heartbeat for 120s → consider stuck


def _start_team_watchdog(
    team_name: str,
    all_names: list[str],
    boss_session,
    timeout: int = _TEAM_WATCHDOG_TIMEOUT,
) -> None:
    """Start a daemon thread that monitors team completion.

    The watchdog handles two degradation scenarios:
    1. **All teammates exited but boss wasn't notified** — synthesizes a
       "team_complete" result message to the boss via BUS, which triggers
       the A2A callback to re-invoke the boss session.
    2. **Team stuck (timeout)** — after `timeout` seconds, sends a
       "team_timeout" result with whatever partial info is available,
       forcing the boss to surface a degraded result to the user.

    The watchdog also detects individually stuck teammates (registered but
    no heartbeat for _TEAM_STUCK_THRESHOLD seconds) and logs warnings.
    """
    import time as _time
    # session_dir() is threading.local — capture here so the watchdog thread
    # writes to the same mailbox as the boss. Without this, BUS.send in the
    # watchdog would write to a different session dir and the boss would
    # never see the notification.
    captured_session_dir = session_dir()

    def _watch():
        set_session_dir(captured_session_dir)
        start = _time.monotonic()
        notified = False
        while not notified:
            _time.sleep(_TEAM_WATCHDOG_INTERVAL)
            elapsed = _time.monotonic() - start
            active = set(registry.active_names())
            team_alive = [n for n in all_names if n in active]

            # Check for stuck teammates (alive but no heartbeat)
            heartbeats = registry.get_heartbeats()
            now_mono = _time.monotonic()
            for n in team_alive:
                last_hb = heartbeats.get(n)
                if last_hb is not None and (now_mono - last_hb) > _TEAM_STUCK_THRESHOLD:
                    logger.warning(
                        "Teammate %s in team %r appears stuck (no heartbeat for %.0fs)",
                        n, team_name, now_mono - last_hb)

            if not team_alive:
                # All teammates exited — notify boss with completion.
                logger.info("Watchdog: team %r complete — all %d members exited "
                            "after %.1fs", team_name, len(all_names), elapsed)
                BUS.send(
                    f"watchdog-{team_name}", "boss",
                    f"[team_complete] Team '{team_name}' finished. "
                    f"All {len(all_names)} member(s) have exited. "
                    f"Check check_inbox for their results.",
                    "result",
                    {"watchdog": True, "team_name": team_name,
                     "elapsed": round(elapsed, 1)})
                notified = True
                break

            if elapsed > timeout:
                # Timeout — force completion with degraded status.
                logger.warning("Watchdog: team %r TIMEOUT after %.0fs — "
                               "still active: %s. Forcing completion.",
                               team_name, elapsed, team_alive)
                BUS.send(
                    f"watchdog-{team_name}", "boss",
                    f"[team_timeout] Team '{team_name}' timed out after "
                    f"{int(elapsed)}s. Still active: {team_alive}. "
                    f"Forcing completion — check check_inbox for partial results.",
                    "result",
                    {"watchdog": True, "team_name": team_name,
                     "timeout": True, "still_active": team_alive,
                     "elapsed": round(elapsed, 1)})
                notified = True
                break

    threading.Thread(target=_watch, name=f"watchdog-{team_name}",
                     daemon=True).start()
    logger.info("Watchdog started for team %r (timeout=%ds, members=%s)",
                team_name, timeout, all_names)


def start_team(team_name: str, task: str = "") -> str:
    """Launch a saved team (from .agents/agents_config.json) in 3-tier mode:
    main loop → team leader → members. Spawns a dedicated LEADER teammate
    plus the predefined members, each in its own git worktree under .worktrees/.
    Returns a coordination summary naming the leader."""
    from agent_core.agents import get_team, list_team_names, get_agent
    from agent_core.worktrees import create_worktree, _worktrees_dir
    from agent_core.tools import LEADER_TOOL_NAMES, MEMBER_TOOL_NAMES
    from agent_core.mcp import get_current_session
    from agent_core.bus import register_bus_tap, unregister_bus_tap

    logger.info("Starting team %r (task=%r)", team_name, task)

    # The boss session is the gateway chat session running this tool call.
    boss_session = get_current_session()
    registry.set_boss_session(team_name, boss_session)

    # Register a bus tap so team conversation is bridged to the frontend.
    def _bus_tap(frm, to, content_t, mtype, meta):
        if mtype not in ("message", "result", "plan_approval_request"):
            return
        mid = (meta or {}).get("request_id") or (
            f"bus_{int(time.time() * 1000)}_{frm}_{to}")
        emit_team_event(boss_session, "team_message", {
            "type": "team.message.p2p",
            "from_member": frm,
            "to_member": to,
            "content": str(content_t)[:1000],
            "message_id": mid,
            "timestamp": int(time.time() * 1000),
        })

    session_key = str(session_dir())
    register_bus_tap(session_key, _bus_tap)

    team = get_team(team_name)
    if team is None:
        avail = list_team_names()
        return (f"No team named {team_name!r}. "
                f"Available: {avail if avail else '(none)'}")

    config = parse_team_config(team_name, team)
    members = resolve_members(config)
    if not members:
        return f"Team {team_name!r} has no members to spawn."

    leader_name = config.leader.effective_name(team_name)
    leader_dname = config.leader.effective_display_name(team_name)
    leader_akey = config.leader.agent_key
    leader_persona = config.leader.persona

    # Register the leader so send_to_leader can address it.
    registry.set_leader(team_name, leader_name)

    # Surface the overall team task to the frontend TeamArea.
    emit_team_event(boss_session, "team_task", {
        "type": TEAM_TASK_CREATED,
        "task_id": f"{team_name}-root",
        "team_name": team_name,
        "title": task or "(no task)",
        "status": "in_progress",
        "timestamp": now_ms(),
    })

    # ── Spawn members first (so the leader's roster is real when it starts) ──
    member_roster: list[tuple[str, str, str | None]] = []
    for m in members:
        mname = m.member_name
        dname = m.effective_display_name

        agent_model = None
        if m.agent_key:
            defn = get_agent(m.agent_key)
            if defn is not None:
                agent_model = defn.get("model") or None

        # Per-member worktree for isolation (best-effort).
        wt_name = f"{team_name}-{mname}"
        wt_path = None
        try:
            res = create_worktree(wt_name)
        except Exception as _wte:
            res = ""
            # T-H4: warn when worktree creation fails — previously this was
            # silently swallowed and the teammate ran in the main workspace
            # with no isolation, which is a security/correctness concern.
            logger.warning("Team %s: worktree creation failed for member %s "
                           "(%s: %s) — teammate will run in the main workspace "
                           "without isolation", team_name, mname,
                           type(_wte).__name__, _wte)
        if isinstance(res, str) and res.startswith("Worktree '"):
            wt_path = str(_worktrees_dir() / wt_name)
        elif not wt_path:
            logger.warning("Team %s: member %s has no worktree isolation "
                           "(create_worktree returned: %r)", team_name, mname, res)

        member_prompt = build_member_prompt(team_name, task, leader_name, m)

        spawn_teammate_thread(
            name=mname, role=dname, prompt=member_prompt,
            agent_key=m.agent_key or None, worktree=wt_path,
            model=agent_model, tool_names=MEMBER_TOOL_NAMES,
            persona=m.persona or None, display_name=dname or None,
            overseer=leader_name,
            boss_session=boss_session, team_name=team_name,
            member_mode="member",
        )
        member_roster.append((mname, dname, wt_path))

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
    except Exception as _wte:
        res = ""
        # T-H4: warn on worktree creation failure for leader too.
        logger.warning("Team %s: worktree creation failed for leader %s "
                       "(%s: %s) — leader will run in the main workspace "
                       "without isolation", team_name, leader_name,
                       type(_wte).__name__, _wte)
    if isinstance(res, str) and res.startswith("Worktree '"):
        leader_wt = str(_worktrees_dir() / wt_name)
    elif not leader_wt:
        logger.warning("Team %s: leader %s has no worktree isolation "
                       "(create_worktree returned: %r)", team_name,
                       leader_name, res)

    roster_for_prompt = [(mn, dn) for mn, dn, _ in member_roster]
    leader_prompt = build_leader_prompt(
        team_name, task, leader_name, leader_dname,
        roster_for_prompt, persona=leader_persona,
    )

    # Collect all worktree names for cleanup on team exit (T-H5).
    all_wt_names = [f"{team_name}-{leader_name}"] + \
                   [f"{team_name}-{mn}" for mn, _, _ in member_roster]

    # Leader exit callback: unregister the bus tap, clear team state, and
    # clean up all worktrees (T-H5: previously worktrees were never deleted,
    # accumulating on disk across team runs).
    def _leader_on_exit():
        unregister_bus_tap(session_key)
        registry.clear_team(team_name)
        # T-H5: clean up worktrees. Use discard_changes=True because the
        # team is done — any uncommitted work was already sent via BUS.
        from agent_core.worktrees import remove_worktree
        for wtn in all_wt_names:
            try:
                remove_worktree(wtn, discard_changes=True)
            except Exception:
                pass
        logger.info("Team %r leader %s exited — bus tap unregistered, "
                    "state cleared, %d worktree(s) cleaned up",
                    team_name, leader_name, len(all_wt_names))

    spawn_teammate_thread(
        name=leader_name, role=leader_dname, prompt=leader_prompt,
        agent_key=leader_akey or None, worktree=leader_wt,
        model=leader_model, tool_names=LEADER_TOOL_NAMES,
        persona=leader_persona or None, display_name=leader_dname or None,
        overseer="boss",
        boss_session=boss_session, team_name=team_name,
        member_mode="leader",
        on_exit=_leader_on_exit,
    )

    # Start the watchdog — detects when all teammates exit (and notifies
    # the boss) or when the team is stuck (timeout → degraded completion).
    all_teammate_names = [leader_name] + [mn for mn, _, _ in member_roster]
    _start_team_watchdog(team_name, all_teammate_names, boss_session)

    logger.info("Team %r launched: leader=%s, %d members (watchdog active)",
                team_name, leader_name, len(member_roster))

    return build_team_summary(
        team_name, leader_name, leader_dname, leader_wt, member_roster,
    )
