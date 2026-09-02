"""evals.metrics.repeatability_metrics — Determinism / pass@k (5.14)."""
from __future__ import annotations

import statistics
from evals.metrics.base import register_metric
from evals.collectors.trace_model import EvalTrace


@register_metric("determinism", "repeatability", "1 - stddev(scores over N runs)", "↑")
def determinism(trace: EvalTrace, task: dict) -> dict:
    # Computed at aggregate level from multiple runs; single-trace returns n/a
    scores = task.get("_repeat_scores", [])
    if len(scores) < 2:
        return {"score": 1.0, "n": len(scores), "note": "need >=2 runs"}
    std = statistics.stdev(scores)
    return {"score": max(0.0, 1.0 - std), "n": len(scores), "stddev": std,
            "mean": statistics.mean(scores)}


@register_metric("pass_at_k", "repeatability", "pass@k estimate", "↑")
def pass_at_k(trace: EvalTrace, task: dict) -> dict:
    scores = task.get("_repeat_scores", [])
    k = task.get("repeat", 1)
    n = len(scores)
    if n == 0:
        return {"rate": 0.0, "n": 0, "k": k}
    passed = sum(1 for s in scores if s >= 0.5)
    # pass@k = 1 - C(n-passed, k) / C(n, k) (approximate)
    rate = passed / n
    return {"rate": rate, "n": n, "k": k, "passed": passed}
