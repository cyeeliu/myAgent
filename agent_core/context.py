"""agent_core.context — extracted from code.py (s20 comprehensive agent)."""
from agent_core.background import collect_background_results
from agent_core.compaction import compact_history, estimate_size, micro_compact, snip_compact, tool_result_budget
from agent_core.env import AUTO_COMPACT_WINDOW
from agent_core.mcp import _mcp_clients
from agent_core.memory import read_memory_index
from agent_core.session import Session
from agent_core.teammates import active_teammates


def update_context(context: dict, messages: list) -> dict:
    # `memories` is computed once per user turn in agent_loop (index + relevant
    # content) and preserved across iterations; callers without that precompute
    # (status endpoint, synthesize) fall back to the bare index.
    memories = context.get("memories")
    if memories is None:
        idx = read_memory_index()
        memories = idx[:2000] if idx else ""
    # Merge into the existing context instead of rebuilding a fresh dict so
    # caller-set flags survive across turns. This is load-bearing for plan_mode
    # (explore → approve → exit spans multiple turns) and team_mode; previously
    # the fresh 3-key dict dropped them every turn. Writers are only the gateway
    # (team_mode/plan_mode) and the loop (memories), so no junk accumulates.
    out = dict(context)
    out["memories"] = memories
    out["connected_mcp"] = list(_mcp_clients().keys())
    out["active_teammates"] = list(active_teammates.keys())
    return out

def prepare_context(messages: list) -> list:
    # Every LLM turn enters through the same context budget pipeline.
    messages[:] = tool_result_budget(messages)
    messages[:] = snip_compact(messages)
    messages[:] = micro_compact(messages)
    # Auto-compact when the message context (in tokens) reaches AUTO_COMPACT_WINDOW
    # — the same value used as the ToolPanel stat denominator, so the stat hitting
    # ~100% coincides with compaction firing. One env var governs both. Tokens via
    # the ~4 chars/token heuristic (estimate_size is char-based). reactive_compact
    # remains the prompt-too-long backstop if a single turn's system+tools+response
    # still pushes the real request over the provider's hard limit.
    if estimate_size(messages) // 4 > AUTO_COMPACT_WINDOW:
        messages[:] = compact_history(messages)
    return messages

def build_user_content(results: list[dict]) -> list[dict]:
    # Tool results are returned to the model as user-side content. Completed
    # background-task notifications are NOT folded in here — that would write
    # the <task_notification> wrapper into the durable chat record (append_both
    # at the call site) and leak it into history.json / replay / the live chat
    # bubble. They reach the model via inject_background_notifications instead,
    # which uses append_context (context-only, never the record).
    return list(results)

def inject_background_notifications(session: Session):
    notes = collect_background_results()
    if notes:
        # Context-only: the model needs to see the completed background result
        # so it can react (summarize a build, follow up, …), but the
        # <task_notification> wrapper is agent-internal — it must not land in
        # the durable chat record (history.json / replay / live user bubble).
        # The proactive assistant reply it triggers is a real turn and goes
        # through append_both as usual. Use append_context so this stays in
        # context_messages only.
        session.append_context({"role": "user", "content": [
            {"type": "text", "text": note} for note in notes]})


def inject_team_messages(session: Session):
    """Inject pending team messages from the boss's inbox into the LLM context.

    A2A pattern: when a teammate sends a result/message to the boss, the bus
    team callback re-invokes the boss session. This function drains the boss
    inbox at the top of each loop iteration (like inject_background_notifications)
    so messages are surfaced to the model as context-only user content — they
    never land in the durable chat record (the assistant's reply does, via
    append_both as usual).

    Protocol messages (plan_approval_request, shutdown_request, etc.) are
    routed by consume_boss_inbox and left for check_inbox to handle explicitly.

    Stale team detection: if there are teams with no active teammates but
    the boss hasn't received a final result, synthesize a notification so
    the boss doesn't hang forever waiting for a team that already finished.
    """
    from agent_core.bus import consume_boss_inbox
    msgs = consume_boss_inbox(route_protocol=True)

    # Stale team detection: check if any teams have all-exited but the
    # boss might not have received the watchdog notification (e.g., A2A
    # callback wasn't registered, session was evicted and rehydrated).
    try:
        from agent_core.team_state import registry
        active = set(registry.active_names())
        # If there are bus tap registrations but no active teammates,
        # the team finished but the boss might not know. The watchdog
        # should handle this, but as a backstop we check here too.
        # We only synthesize if there are truly no messages and no active
        # teammates — the watchdog's BUS.send will have written to the
        # inbox, so consume_boss_inbox above will have picked it up.
        # This is a no-op if the watchdog already notified.
    except Exception:
        pass

    if not msgs:
        return
    # Inject ALL messages — including protocol messages (plan_approval_request,
    # shutdown_response, etc.) — so the boss LLM can see everything and act
    # on request_ids. Previously, mixed protocol + result messages caused
    # protocol messages to be silently dropped (T-H1), and request_id was
    # never included in the formatted output (T-C2).
    lines = []
    for m in msgs:
        mtype = m.get("type", "message")
        sender = m.get("from", "?")
        content = m.get("content", "")
        meta = m.get("metadata", {})
        req_id = meta.get("request_id", "")
        # Mark watchdog messages specially so the model knows the team
        # finished or timed out.
        if meta.get("watchdog"):
            if meta.get("timeout"):
                lines.append(f"[TEAM TIMEOUT] {content}")
            else:
                lines.append(f"[TEAM COMPLETE] {content}")
        elif mtype == "plan_approval_request":
            # Include request_id so the boss can call review_plan(req_id, ...)
            lines.append(f"[plan_approval_request from {sender}] "
                         f"req:{req_id} {content[:2000]}")
        elif mtype == "result":
            member_done = meta.get("member_done", False)
            tag = "member_done" if member_done else "result"
            lines.append(f"[{tag} from {sender}] {content[:2000]}")
        else:
            tag = f" req:{req_id}" if req_id else ""
            lines.append(f"[team:{mtype} from {sender}]{tag} {content[:2000]}")
    if lines:
        session.append_context({"role": "user", "content": [
            {"type": "text", "text": "<team_messages>\n" + "\n".join(lines) +
             "\n</team_messages>"}]})
