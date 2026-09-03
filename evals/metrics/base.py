"""evals.metrics.base — metric registry and compute_all_metrics.

Each metric module registers its compute functions here. compute_all_metrics
runs all registered metrics and returns a flat dict[metric_name → value].
"""
from __future__ import annotations

from typing import Any, Callable

from evals.collectors.trace_model import EvalTrace


# Registry: metric_name → (compute_fn, category, description, direction)
_METRIC_REGISTRY: dict[str, tuple[Callable, str, str, str]] = {}


def register_metric(name: str, category: str, description: str, direction: str = "↑"):
    """Decorator to register a metric compute function.

    direction: "↑" (higher is better), "↓" (lower is better), "诊断" (diagnostic)
    """
    def decorator(fn: Callable):
        _METRIC_REGISTRY[name] = (fn, category, description, direction)
        return fn
    return decorator


def compute_all_metrics(trace: EvalTrace, task: dict | None = None) -> dict[str, Any]:
    """Run all registered metrics and return a flat dict.

    Also returns a nested dict grouped by category.
    """
    flat: dict[str, Any] = {}
    nested: dict[str, dict] = {}

    for name, (fn, category, desc, direction) in _METRIC_REGISTRY.items():
        try:
            value = fn(trace, task or {})
        except Exception as e:
            value = {"error": str(e)}
        flat[name] = value
        nested.setdefault(category, {})[name] = {
            "value": value,
            "description": desc,
            "direction": direction,
        }

    flat["_categories"] = nested
    return flat


def list_metrics() -> dict[str, dict]:
    """List all registered metrics with their metadata."""
    return {
        name: {"category": cat, "description": desc, "direction": direction}
        for name, (_, cat, desc, direction) in _METRIC_REGISTRY.items()
    }
