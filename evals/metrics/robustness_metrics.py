"""evals.metrics.robustness_metrics — Error rate & recovery (5.8), context budget hit (5.16)."""
from __future__ import annotations

from evals.metrics.base import register_metric
from evals.collectors.trace_model import EvalTrace


@register_metric("error_rate", "robustness", "Error events / turns", "↓")
def error_rate(trace: EvalTrace, task: dict) -> dict:
    errors = sum(1 for e in trace.events if e["kind"] == "error")
    turns = len(trace.turns) or 1
    return {"rate": errors / turns, "errors": errors, "turns": turns}


@register_metric("recovery_rate", "robustness", "Turns that succeeded after reactive_compact", "↑")
def recovery_rate(trace: EvalTrace, task: dict) -> dict:
    reactive_compacts = sum(1 for e in trace.events
                            if e["kind"] == "compacted"
                            and e["payload"].get("reason") == "reactive_compact")
    # A turn is "recovered" if it had compaction but no subsequent error before done
    recovered = 0
    for t in trace.turns:
        if t.had_compaction and not t.had_error:
            recovered += 1
    total_compacted = sum(1 for t in trace.turns if t.had_compaction) or 1
    return {"rate": recovered / total_compacted, "recovered": recovered,
            "reactive_compacts": reactive_compacts}


@register_metric("max_tokens_rate", "robustness", "Turns ending with max_tokens / total", "↓")
def max_tokens_rate(trace: EvalTrace, task: dict) -> dict:
    turns = trace.turns
    if not turns:
        return {"rate": 0.0, "count": 0, "total": 0}
    mt = sum(1 for t in turns if t.stop_reason == "max_tokens")
    return {"rate": mt / len(turns), "count": mt, "total": len(turns)}


@register_metric("context_budget_hit", "robustness", "1 - reactive_compacts / turns", "↑")
def context_budget_hit(trace: EvalTrace, task: dict) -> dict:
    turns = len(trace.turns) or 1
    reactive = sum(1 for e in trace.events
                   if e["kind"] == "compacted"
                   and e["payload"].get("reason") == "reactive_compact")
    return {"score": max(0.0, 1.0 - reactive / turns), "reactive_compacts": reactive, "turns": turns}
