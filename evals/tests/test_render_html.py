"""Tests for evals.report.render HTML output (E-F7)."""
from __future__ import annotations

from pathlib import Path

from evals.report.render import render_html, render_compare_html


def _report(run_id="r1", pr=0.5, ms=0.6, tasks=None, scorecard=None):
    return {
        "run_id": run_id, "dataset": "ds", "model": "m", "mode": "online",
        "total_tasks": 1, "total_runs": 1,
        "judge_pass_rate": pr, "judge_mean_score": ms,
        "scorecard": scorecard or {"cost.cost_usd": {"mean": 0.01, "stddev": 0.0, "min": 0.01, "max": 0.01}},
        "per_task": tasks or [{"task_id": "t1", "reps": 1, "mean_score": ms, "pass_count": 1, "status": "ok"}],
        "results": [],
    }


def test_render_html_writes_file(tmp_path):
    path = render_html(_report(), tmp_path)
    assert path.exists()
    html = path.read_text()
    assert "Evaluation Report" in html
    assert "Scorecard" in html
    assert "t1" in html


def test_render_html_includes_regression_section(tmp_path):
    report = _report()
    report["regression"] = {"regressed": ["t2"], "fixed": ["t3"], "baseline_run_id": "base"}
    path = render_html(report, tmp_path)
    html = path.read_text()
    assert "Regression" in html
    assert "t2" in html and "t3" in html


def test_render_compare_html_distinguishes_newly_passing_failing(tmp_path):
    a = _report(run_id="a", tasks=[
        {"task_id": "t1", "reps": 1, "mean_score": 0.5, "pass_count": 1, "status": "ok"},
        {"task_id": "t2", "reps": 1, "mean_score": 0.2, "pass_count": 0, "status": "ok"},
    ])
    b = _report(run_id="b", pr=0.5, tasks=[
        {"task_id": "t1", "reps": 1, "mean_score": 0.2, "pass_count": 0, "status": "ok"},
        {"task_id": "t2", "reps": 1, "mean_score": 0.8, "pass_count": 1, "status": "ok"},
    ])
    path = render_compare_html(a, b, tmp_path)
    html = path.read_text()
    assert "t2" in html  # newly passing
    assert "t1" in html  # newly failing
    assert "Δ" in html or "mean" in html
