"""Tests for evals.engine.parallel opts-driven behavior (E-A4/E-F5/E-F10/E-M2).

These monkeypatch ``_run_one`` so no real agent_loop/LLM is invoked — they
verify the ParallelRunner honors opts (max_workers, limit, repeat, cancel,
only_failed_from, regression_baseline) and aggregates correctly.
"""
from __future__ import annotations

import threading

import pytest

from evals.engine.parallel import ParallelRunner
from evals.engine.runner import TaskResult


def _dataset(n=4) -> dict:
    return {"name": "ds", "tasks": [{"id": f"t{i}", "prompt": "p"} for i in range(n)]}


def test_max_workers_read_from_opts(monkeypatch):
    calls = []
    def fake_run_one(self, task, rep, run_id, model, mode, cancel_event=None):
        calls.append(task["id"])
        return TaskResult(task_id=task["id"], rep=rep, status="ok",
                          judge={"score": 1.0, "passed": True})
    monkeypatch.setattr(ParallelRunner, "_run_one", fake_run_one)
    runner = ParallelRunner()
    report = runner.run_dataset(_dataset(4), {"max_workers": 2, "mode": "mock"})
    assert sorted(calls) == ["t0", "t1", "t2", "t3"]
    assert report["total_tasks"] == 4


def test_limit_slicing_from_opts(monkeypatch):
    seen = []
    monkeypatch.setattr(ParallelRunner, "_run_one",
                        lambda self, t, r, rid, m, mode, cancel_event=None:
                        (seen.append(t["id"]),
                         TaskResult(task_id=t["id"], rep=r, status="ok",
                                    judge={"score": 1.0, "passed": True}))[1])
    runner = ParallelRunner()
    report = runner.run_dataset(_dataset(4), {"limit": 2, "mode": "mock"})
    assert sorted(seen) == ["t0", "t1"]
    assert report["total_tasks"] == 2


def test_repeat_multiplies_work_items(monkeypatch):
    reps = []
    monkeypatch.setattr(ParallelRunner, "_run_one",
                        lambda self, t, r, rid, m, mode, cancel_event=None:
                        (reps.append((t["id"], r)),
                         TaskResult(task_id=t["id"], rep=r, status="ok",
                                    judge={"score": 1.0, "passed": True}))[1])
    runner = ParallelRunner()
    runner.run_dataset(_dataset(2), {"repeat": 3, "mode": "online"})
    # 2 tasks × 3 reps = 6 work items
    assert len(reps) == 6
    assert sum(1 for tid, _ in reps if tid == "t0") == 3


def test_cancel_event_stops_submission(monkeypatch):
    ev = threading.Event()
    ev.set()  # pre-cancelled
    monkeypatch.setattr(ParallelRunner, "_run_one",
                        lambda self, t, r, rid, m, mode, cancel_event=None:
                        TaskResult(task_id=t["id"], rep=r, status="ok",
                                   judge={"score": 1.0, "passed": True}))
    runner = ParallelRunner()
    report = runner.run_dataset(_dataset(4), {"cancel_event": ev, "mode": "mock"})
    # Nothing submitted.
    assert report["total_runs"] == 0


def test_only_failed_filter(monkeypatch):
    # Pretend a baseline run exists with t0/t1 failed.
    from evals.engine.runner import EvalRunner
    def fake_filter(self, tasks, run_id):
        return [t for t in tasks if t["id"] in ("t0", "t1")]
    monkeypatch.setattr(EvalRunner, "_filter_to_failed", fake_filter)
    monkeypatch.setattr(ParallelRunner, "_run_one",
                        lambda self, t, r, rid, m, mode, cancel_event=None:
                        TaskResult(task_id=t["id"], rep=r, status="ok",
                                   judge={"score": 1.0, "passed": True}))
    runner = ParallelRunner()
    report = runner.run_dataset(_dataset(4),
                                {"only_failed_from": "baseline", "mode": "mock"})
    assert report["total_tasks"] == 2


def test_regression_detection_invoked(monkeypatch):
    from evals.engine.runner import EvalRunner
    called = {}
    def fake_detect(self, current, baseline_run_id):
        called["baseline"] = baseline_run_id
        return {"regressed": [], "fixed": [], "baseline_run_id": baseline_run_id}
    monkeypatch.setattr(EvalRunner, "_detect_regression", fake_detect)
    monkeypatch.setattr(ParallelRunner, "_run_one",
                        lambda self, t, r, rid, m, mode, cancel_event=None:
                        TaskResult(task_id=t["id"], rep=r, status="ok",
                                   judge={"score": 1.0, "passed": True}))
    runner = ParallelRunner()
    report = runner.run_dataset(_dataset(2),
                                {"regression_baseline": "base", "mode": "mock"})
    assert called.get("baseline") == "base"
    assert "regression" in report
