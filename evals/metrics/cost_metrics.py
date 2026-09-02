"""evals.metrics.cost_metrics — Cost estimation (5.15)."""
from __future__ import annotations

from evals.metrics.base import register_metric
from evals.collectors.trace_model import EvalTrace

# Price table (per 1K tokens, USD). Extend as needed.
_PRICE_TABLE = {
    "gpt-4": {"in": 0.03, "out": 0.06},
    "gpt-4o": {"in": 0.0025, "out": 0.01},
    "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
    "glm-5": {"in": 0.002, "out": 0.006},
    "glm-5.2": {"in": 0.002, "out": 0.006},
    "test-model": {"in": 0.0, "out": 0.0},
}


@register_metric("cost", "cost", "Estimated cost per task", "↓")
def cost(trace: EvalTrace, task: dict) -> dict:
    model = trace.meta.get("model", "unknown")
    totals = trace.meta.get("token_totals", {})
    tin = totals.get("tokens_in", 0)
    tout = totals.get("tokens_out", 0)
    prices = _PRICE_TABLE.get(model, {"in": 0.002, "out": 0.006})  # default estimate
    cost_usd = (tin / 1000) * prices["in"] + (tout / 1000) * prices["out"]
    return {"cost_usd": cost_usd, "model": model,
            "tokens_in": tin, "tokens_out": tout,
            "price_in": prices["in"], "price_out": prices["out"]}
