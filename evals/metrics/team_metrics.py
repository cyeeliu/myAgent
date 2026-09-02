"""evals.metrics.team_metrics — Team coordination (5.12)."""
from __future__ import annotations

from collections import Counter
from evals.metrics.base import register_metric
from evals.collectors.trace_model import EvalTrace


@register_metric("team_coordination", "team", "Team message exchange and handshake metrics", "诊断")
def team_coordination(trace: EvalTrace, task: dict) -> dict:
    team_events = [e for e in trace.events if e["kind"].startswith("team_")]
    messages = [e for e in team_events if e["kind"] == "team_message"]
    # Detect redundant work: same tool called by multiple members
    tool_counter = Counter(tc.name for tc in trace.tool_calls)
    redundant = {name: cnt for name, cnt in tool_counter.items() if cnt > 2}
    return {
        "messages_exchanged": len(messages),
        "team_events": len(team_events),
        "redundant_tools": redundant,
    }
