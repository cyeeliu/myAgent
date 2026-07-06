"""agent_core.context — extracted from code.py (s20 comprehensive agent)."""
from agent_core.background import collect_background_results
from agent_core.compaction import compact_history, estimate_size, micro_compact, snip_compact, tool_result_budget
from agent_core.env import CONTEXT_LIMIT
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
    return {
        "memories": memories,
        "connected_mcp": list(_mcp_clients().keys()),
        "active_teammates": list(active_teammates.keys()),
    }

def prepare_context(messages: list) -> list:
    # Every LLM turn enters through the same context budget pipeline.
    messages[:] = tool_result_budget(messages)
    messages[:] = snip_compact(messages)
    messages[:] = micro_compact(messages)
    if estimate_size(messages) > CONTEXT_LIMIT:
        messages[:] = compact_history(messages)
    return messages

def build_user_content(results: list[dict]) -> list[dict]:
    # Tool results and completed background notifications are both returned to
    # the model as user-side content, matching the tool_result feedback loop.
    content = list(results)
    for note in collect_background_results():
        content.append({"type": "text", "text": note})
    return content

def inject_background_notifications(session: Session):
    notes = collect_background_results()
    if notes:
        session.append_both({"role": "user", "content": [
            {"type": "text", "text": note} for note in notes]})
