"""evals.metrics.tool_metrics — Tool call success rate (5.1), selection accuracy (5.2), argument validity (5.3)."""
from __future__ import annotations

from collections import Counter
from evals.metrics.base import register_metric
from evals.collectors.trace_model import EvalTrace


@register_metric("tool_success_rate", "tool", "Successful tool calls / total calls", "↑")
def tool_success_rate(trace: EvalTrace, task: dict) -> dict:
    calls = trace.tool_calls
    if not calls:
        return {"rate": 1.0, "total": 0, "success": 0, "failed": 0}
    success = sum(1 for c in calls if c.success)
    total = len(calls)
    return {"rate": success / total, "total": total, "success": success, "failed": total - success}


@register_metric("tool_success_by_name", "tool", "Success rate per tool name", "↑")
def tool_success_by_name(trace: EvalTrace, task: dict) -> dict:
    by_name: dict[str, dict] = {}
    for c in trace.tool_calls:
        d = by_name.setdefault(c.name, {"total": 0, "success": 0, "rate": 0.0})
        d["total"] += 1
        if c.success:
            d["success"] += 1
    for name, d in by_name.items():
        d["rate"] = d["success"] / d["total"] if d["total"] else 0.0
    return by_name


@register_metric("tool_error_breakdown", "tool", "Error counts by error_kind", "诊断")
def tool_error_breakdown(trace: EvalTrace, task: dict) -> dict:
    counts = Counter(c.error_kind or "none" for c in trace.tool_calls if not c.success)
    return dict(counts)


@register_metric("tool_selection_score", "tool", "Tool selection accuracy vs expected/forbidden", "↑")
def tool_selection_score(trace: EvalTrace, task: dict) -> dict:
    expected = set(task.get("expected_tools", []))
    forbidden = set(task.get("forbidden_tools", []))
    used = set(c.name for c in trace.tool_calls)
    if not expected:
        return {"score": 1.0, "expected": [], "used": list(used), "forbidden_hit": []}
    overlap = len(used & expected)
    score = overlap / len(expected)
    forbidden_hit = used & forbidden
    score -= 0.5 * len(forbidden_hit)
    score = max(0.0, min(1.0, score))
    return {"score": score, "expected": list(expected), "used": list(used),
            "forbidden_hit": list(forbidden_hit)}


@register_metric("tool_arg_validity", "tool", "1 - arg_errors / total", "↑")
def tool_arg_validity(trace: EvalTrace, task: dict) -> dict:
    calls = trace.tool_calls
    if not calls:
        return {"rate": 1.0, "arg_errors": 0, "total": 0}
    arg_errors = sum(1 for c in calls if c.error_kind in ("schema", "path_escape"))
    total = len(calls)
    return {"rate": 1 - arg_errors / total, "arg_errors": arg_errors, "total": total}
