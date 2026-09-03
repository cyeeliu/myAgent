"""evals.api — FastAPI router for evaluation control-plane endpoints.

Control plane: start / list / get / cancel / compare runs.
Data plane (large trace/report downloads) is served by ``agent_gateway.routes.eval``
which includes this router.

E-F9: This router is mounted into the gateway via
``agent_gateway.routes.eval`` (``router.include_router(eval_control_router)``)
under the ``/api/eval`` prefix, so the REST eval interface is live whenever the
gateway is running. It can also be mounted standalone:
    from evals.api import router
    app.include_router(router, prefix="/api/eval")

E-L1: all filesystem paths are REPO_ROOT-anchored. E-F5: run cancellation is
exposed via a per-run ``threading.Event``. E-F7: ``/compare`` returns the full
scorecard + per-task diff (not just two numbers).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, BackgroundTasks
    from pydantic import BaseModel
except ImportError:  # pragma: no cover — fastapi optional for lib-only use
    APIRouter = None  # type: ignore

# E-L1: REPO_ROOT-anchored paths.
try:
    from agent_core.paths import REPO_ROOT
except Exception:  # pragma: no cover
    REPO_ROOT = Path.cwd()

_DATASETS_ROOT = REPO_ROOT / "evals" / "datasets"

# E-F5: track active runs for cancellation: run_id → {cancel_event, thread, status}
_ACTIVE_RUNS: dict[str, dict] = {}
_ACTIVE_LOCK = threading.Lock()


if APIRouter:
    router = APIRouter()

    class RunRequest(BaseModel):
        dataset: str
        model: str | None = None
        mode: str = "online"
        limit: int | None = None
        repeat: int = 1
        only_failed_from: str | None = None
        regression_baseline: str | None = None
        judge_model: str | None = None
        max_workers: int | None = None

    class CompareRequest(BaseModel):
        run_a: str
        run_b: str

    def _load_dataset_file(name: str) -> dict:
        # E-L1: REPO_ROOT-anchored dataset lookup.
        dataset_path = _DATASETS_ROOT / f"{name}.json"
        if not dataset_path.exists():
            p = Path(name)
            if not p.exists():
                raise HTTPException(404, f"dataset not found: {name}")
            dataset_path = p
        return json.loads(dataset_path.read_text())

    @router.post("/runs")
    async def start_run(req: RunRequest, bg: BackgroundTasks):
        """Start an evaluation run (async, returns run_id)."""
        from evals.engine.runner import EvalRunner
        from evals.storage.results import ResultStore

        dataset = _load_dataset_file(req.dataset)

        # E-Q12: inject dataset-wide judge model.
        if req.judge_model:
            for task in dataset.get("tasks", []):
                task.setdefault("judge_model", req.judge_model)

        runner = EvalRunner()
        store = ResultStore()

        # Generate run_id first so we can track/cancel it.
        run_id = runner._make_run_id(dataset)
        cancel_event = threading.Event()
        opts: dict = {
            "model": req.model, "mode": req.mode, "run_id": run_id,
            "cancel_event": cancel_event,
        }
        if req.limit:
            opts["limit"] = req.limit
        if req.repeat and req.repeat > 1:
            opts["repeat"] = req.repeat
        if req.only_failed_from:
            opts["only_failed_from"] = req.only_failed_from
        if req.regression_baseline:
            opts["regression_baseline"] = req.regression_baseline

        with _ACTIVE_LOCK:
            _ACTIVE_RUNS[run_id] = {"cancel_event": cancel_event, "status": "running"}

        def _run():
            try:
                report = runner.run_dataset(dataset, opts)
                store.save(report)
            except Exception as e:  # pragma: no cover
                with _ACTIVE_LOCK:
                    if run_id in _ACTIVE_RUNS:
                        _ACTIVE_RUNS[run_id]["status"] = f"error: {e}"
                return
            with _ACTIVE_LOCK:
                if run_id in _ACTIVE_RUNS:
                    _ACTIVE_RUNS[run_id]["status"] = "done"

        bg.add_task(_run)
        return {"run_id": run_id, "status": "started"}

    @router.get("/runs")
    async def list_runs():
        """List all evaluation runs (persisted + active)."""
        from evals.storage.results import ResultStore
        store = ResultStore()
        persisted = store.list_runs()
        with _ACTIVE_LOCK:
            active = {rid: info["status"] for rid, info in _ACTIVE_RUNS.items()}
        return {"runs": persisted, "active": active}

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str):
        """Get a run's full report + scorecard."""
        from evals.storage.results import ResultStore
        store = ResultStore()
        report = store.load(run_id)
        if not report:
            raise HTTPException(404, f"run not found: {run_id}")
        return report

    @router.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        """E-F5: request cancellation of an active run."""
        with _ACTIVE_LOCK:
            info = _ACTIVE_RUNS.get(run_id)
        if not info:
            raise HTTPException(404, f"run not active or unknown: {run_id}")
        info["cancel_event"].set()
        info["status"] = "cancelling"
        return {"run_id": run_id, "status": "cancelling"}

    @router.post("/compare")
    async def compare_runs(req: CompareRequest):
        """E-F7: full A/B comparison — scorecard diff + per-task status diff."""
        from evals.storage.results import ResultStore
        store = ResultStore()
        a = store.load(req.run_a)
        b = store.load(req.run_b)
        if not a or not b:
            raise HTTPException(404, "one or both runs not found")

        sc_a, sc_b = a.get("scorecard", {}), b.get("scorecard", {})
        metric_diff = {}
        for key in sorted(set(sc_a) | set(sc_b)):
            va = sc_a.get(key, {}).get("mean", 0.0)
            vb = sc_b.get(key, {}).get("mean", 0.0)
            metric_diff[key] = {"a": va, "b": vb, "delta": vb - va}

        def _pass_map(rep):
            return {t.get("task_id"): (t.get("pass_count", 0) > 0)
                    for t in rep.get("per_task", [])}
        pa, pb = _pass_map(a), _pass_map(b)
        newly_pass = [t for t in pb if pb[t] and not pa.get(t, False)]
        newly_fail = [t for t in pa if pa[t] and not pb.get(t, False)]

        return {
            "run_a": {"run_id": req.run_a,
                      "pass_rate": a.get("judge_pass_rate"),
                      "mean_score": a.get("judge_mean_score")},
            "run_b": {"run_id": req.run_b,
                      "pass_rate": b.get("judge_pass_rate"),
                      "mean_score": b.get("judge_mean_score")},
            "delta": {
                "pass_rate": (b.get("judge_pass_rate", 0) - a.get("judge_pass_rate", 0)),
                "mean_score": (b.get("judge_mean_score", 0) - a.get("judge_mean_score", 0)),
            },
            "metric_diff": metric_diff,
            "newly_passing": newly_pass,
            "newly_failing": newly_fail,
        }
