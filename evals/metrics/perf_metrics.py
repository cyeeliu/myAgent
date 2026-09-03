"""evals.metrics.perf_metrics — Token efficiency (5.6), latency breakdown (5.7), convergence (5.9)."""
from __future__ import annotations

import statistics
from collections import defaultdict
from evals.metrics.base import register_metric
from evals.collectors.trace_model import EvalTrace


@register_metric("tokens_per_task", "perf", "Total tokens consumed per task", "↓")
def tokens_per_task(trace: EvalTrace, task: dict) -> dict:
    totals = trace.meta.get("token_totals", {})
    tin = totals.get("tokens_in", 0)
    tout = totals.get("tokens_out", 0)
    ratio = tout / tin if tin else 0.0
    return {"tokens_in": tin, "tokens_out": tout, "total": tin + tout, "output_ratio": ratio}


@register_metric("latency_breakdown", "perf", "P50/P95/P99 latency per span name", "↓")
def latency_breakdown(trace: EvalTrace, task: dict) -> dict:
    by_name: dict[str, list[float]] = defaultdict(list)
    for s in trace.spans:
        name = s.get("name", "")
        dur = s.get("duration_ms", 0)
        if name and dur:
            by_name[name].append(dur)
    # Also from tool_calls
    for tc in trace.tool_calls:
        if tc.duration_ms:
            by_name[f"tool:{tc.name}"].append(tc.duration_ms)
    result: dict[str, dict] = {}
    for name, durs in by_name.items():
        durs.sort()
        n = len(durs)
        result[name] = {
            "count": n,
            "p50": durs[n // 2] if n else 0,
            "p95": durs[int(n * 0.95)] if n else 0,
            "p99": durs[int(n * 0.99)] if n else 0,
            "mean": statistics.mean(durs) if durs else 0,
        }
    return result


@register_metric("tool_time_ratio", "perf", "Tool time / total turn time", "↓")
def tool_time_ratio(trace: EvalTrace, task: dict) -> dict:
    tool_time = sum(tc.duration_ms for tc in trace.tool_calls)
    turn_time = sum(t.duration_ms for t in trace.turns)
    ratio = tool_time / turn_time if turn_time else 0.0
    return {"ratio": ratio, "tool_ms": tool_time, "turn_ms": turn_time}


@register_metric("turns_to_complete", "perf", "Number of turns to complete", "↓")
def turns_to_complete(trace: EvalTrace, task: dict) -> dict:
    return {"turns": len(trace.turns)}
