"""evals.metrics.cost_metrics — Cost estimation (5.15).

E-Q13: the price table is loaded from an external JSON config when available
(``evals/prices.json`` at REPO_ROOT, or the path in ``$EVALES_PRICES_PATH``),
falling back to a built-in table. New models can be added without editing code.

Price file format (per 1K tokens, USD):
    {
      "gpt-4o": {"in": 0.0025, "out": 0.01},
      "my-model": {"in": 0.001, "out": 0.003}
    }
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from evals.metrics.base import register_metric
from evals.collectors.trace_model import EvalTrace

# Built-in price table (per 1K tokens, USD). Used as fallback when no external
# config is present, and as the base that external entries override/extend.
_BUILTIN_PRICES: dict[str, dict[str, float]] = {
    "gpt-4": {"in": 0.03, "out": 0.06},
    "gpt-4o": {"in": 0.0025, "out": 0.01},
    "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
    "glm-5": {"in": 0.002, "out": 0.006},
    "glm-5.2": {"in": 0.002, "out": 0.006},
    "test-model": {"in": 0.0, "out": 0.0},
}

# Default estimate for models not in the table.
_DEFAULT_PRICE = {"in": 0.002, "out": 0.006}


def _load_price_table() -> dict[str, dict[str, float]]:
    """E-Q13: load prices from external config, merged over the built-ins."""
    table = dict(_BUILTIN_PRICES)
    candidates: list[Path] = []
    env_path = os.environ.get("EVALES_PRICES_PATH")
    if env_path:
        candidates.append(Path(env_path))
    try:
        from agent_core.paths import REPO_ROOT
    except Exception:  # pragma: no cover
        REPO_ROOT = Path.cwd()
    candidates.append(REPO_ROOT / "evals" / "prices.json")
    for cand in candidates:
        try:
            if cand.exists():
                data = json.loads(cand.read_text())
                if isinstance(data, dict):
                    for model, prices in data.items():
                        if isinstance(prices, dict):
                            table[model] = {
                                "in": float(prices.get("in", _DEFAULT_PRICE["in"])),
                                "out": float(prices.get("out", _DEFAULT_PRICE["out"])),
                            }
        except Exception:
            pass  # bad config file → fall back silently
    return table


_PRICE_TABLE = _load_price_table()


@register_metric("cost", "cost", "Estimated cost per task", "↓")
def cost(trace: EvalTrace, task: dict) -> dict:
    model = trace.meta.get("model", "unknown")
    totals = trace.meta.get("token_totals", {})
    tin = totals.get("tokens_in", 0)
    tout = totals.get("tokens_out", 0)
    prices = _PRICE_TABLE.get(model, _DEFAULT_PRICE)
    cost_usd = (tin / 1000) * prices["in"] + (tout / 1000) * prices["out"]
    return {"cost_usd": cost_usd, "model": model,
            "tokens_in": tin, "tokens_out": tout,
            "price_in": prices["in"], "price_out": prices["out"]}
