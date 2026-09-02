"""evals.metrics.context_metrics — Context management (5.5): compaction freq, peak usage, recovery."""
from __future__ import annotations

from evals.metrics.base import register_metric
from evals.collectors.trace_model import EvalTrace


@register_metric("compaction_freq", "context", "Compaction events / turns", "↓")
def compaction_freq(trace: EvalTrace, task: dict) -> dict:
    compactions = sum(1 for e in trace.events if e["kind"] == "compacted")
    turns = len(trace.turns) or 1
    return {"freq": compactions / turns, "compactions": compactions, "turns": turns}


@register_metric("peak_ctx_rate", "context", "Max context usage rate", "诊断")
def peak_ctx_rate(trace: EvalTrace, task: dict) -> dict:
    rates = [e["payload"].get("rate", 0) for e in trace.events
             if e["kind"] == "context_usage"]
    if not rates:
        return {"peak": 0.0, "samples": 0}
    return {"peak": max(rates), "samples": len(rates), "mean": sum(rates) / len(rates)}


@register_metric("compaction_recovery", "context", "Turns completing after compaction without error", "↑")
def compaction_recovery(trace: EvalTrace, task: dict) -> dict:
    compacted_turns = [t for t in trace.turns if t.had_compaction]
    if not compacted_turns:
        return {"rate": 1.0, "compacted_turns": 0}
    recovered = sum(1 for t in compacted_turns if not t.had_error)
    return {"rate": recovered / len(compacted_turns),
            "compacted_turns": len(compacted_turns), "recovered": recovered}
