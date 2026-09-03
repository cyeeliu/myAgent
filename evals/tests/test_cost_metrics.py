"""Tests for evals.metrics.cost_metrics external price config (E-Q13)."""
from __future__ import annotations

import json
import os

import pytest

from evals.metrics import cost_metrics
from evals.collectors.trace_model import EvalTrace


def _trace(model: str, tin: int = 1000, tout: int = 500) -> EvalTrace:
    return EvalTrace(task_id="t", mode="mock",
                     meta={"model": model, "token_totals": {"tokens_in": tin, "tokens_out": tout}})


def test_builtin_prices_present():
    assert "glm-5" in cost_metrics._BUILTIN_PRICES
    assert "test-model" in cost_metrics._BUILTIN_PRICES


def test_cost_calculation_uses_table():
    # test-model is free in the built-in table.
    result = cost_metrics.cost(_trace("test-model", 1000, 1000), {})
    assert result["cost_usd"] == 0.0
    # glm-5: in=0.002/out=0.006 per 1K → 1000 in + 1000 out = 0.002 + 0.006
    result = cost_metrics.cost(_trace("glm-5", 1000, 1000), {})
    assert result["cost_usd"] == pytest.approx(0.008)


def test_external_price_file_overrides(tmp_path, monkeypatch):
    prices = {"glm-5": {"in": 0.5, "out": 0.5}, "brand-new-model": {"in": 0.1, "out": 0.2}}
    p = tmp_path / "prices.json"
    p.write_text(json.dumps(prices))
    monkeypatch.setenv("EVALES_PRICES_PATH", str(p))
    # Reload the table.
    table = cost_metrics._load_price_table()
    assert table["glm-5"]["in"] == 0.5
    assert "brand-new-model" in table
    # Built-ins not overridden are preserved.
    assert "test-model" in table


def test_unknown_model_uses_default_estimate():
    result = cost_metrics.cost(_trace("never-heard-of-it", 1000, 1000), {})
    # default in=0.002 out=0.006 → 0.008
    assert result["cost_usd"] == pytest.approx(0.008)
    assert result["price_in"] == cost_metrics._DEFAULT_PRICE["in"]
