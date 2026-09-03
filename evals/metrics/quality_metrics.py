"""evals.metrics.quality_metrics — Task completion quality (5.10)."""
from __future__ import annotations

from evals.metrics.base import register_metric
from evals.collectors.trace_model import EvalTrace


@register_metric("task_completion", "quality", "Whether the task completed (done event present)", "↑")
def task_completion(trace: EvalTrace, task: dict) -> dict:
    has_done = any(e["kind"] == "done" for e in trace.events)
    has_error = any(e["kind"] == "error" for e in trace.events)
    return {"completed": has_done, "had_error": has_error,
            "score": 1.0 if has_done and not has_error else 0.0}
