"""Tests for evals.report.aggregate repeatability population (E-Q14)."""
from __future__ import annotations

import pytest

from evals.engine.runner import TaskResult
from evals.report.aggregate import aggregate_results


def _result(task_id: str, rep: int, score: float, passed: bool, status: str = "ok") -> TaskResult:
    return TaskResult(task_id=task_id, rep=rep, status=status,
                      judge={"score": score, "passed": passed})


def test_repeatability_populated_for_multiple_reps():
    results = [
        _result("t1", 0, 0.8, True),
        _result("t1", 1, 0.6, True),
        _result("t1", 2, 0.4, False),
    ]
    report = aggregate_results(results, {"name": "ds"}, "rid", "m", "online", {})
    pt = report["per_task"][0]
    assert "repeatability" in pt
    rep = pt["repeatability"]
    assert rep["n"] == 3
    assert 0.0 <= rep["determinism"] <= 1.0
    assert rep["pass_at_k"] == pytest.approx(2 / 3)
    # Scorecard surfaces aggregate repeatability.
    assert "repeatability.determinism" in report["scorecard"]
    assert "repeatability.pass_at_k" in report["scorecard"]


def test_repeatability_single_rep_notes_need_more():
    results = [_result("t1", 0, 0.8, True)]
    report = aggregate_results(results, {"name": "ds"}, "rid", "m", "online", {})
    pt = report["per_task"][0]
    assert pt["repeatability"]["n"] == 1
    assert "need" in pt["repeatability"].get("note", "")


def test_error_reps_excluded_from_repeatability():
    results = [
        _result("t1", 0, 0.8, True),
        _result("t1", 1, 0.0, False, status="error"),
    ]
    report = aggregate_results(results, {"name": "ds"}, "rid", "m", "online", {})
    pt = report["per_task"][0]
    # Only one ok rep → no stddev.
    assert pt["repeatability"]["n"] == 1
