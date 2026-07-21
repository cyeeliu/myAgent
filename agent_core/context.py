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
