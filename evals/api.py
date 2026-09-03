"""evals.api — optional FastAPI router for evaluation endpoints.

Mount this in agent_gateway if REST eval access is needed.
This is a new file — does not modify any existing gateway routes.

Usage (in agent_gateway, if desired):
    from evals.api import router
    app.include_router(router, prefix="/api/eval")
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, BackgroundTasks
    from pydantic import BaseModel
except ImportError:
    APIRouter = None

if APIRouter:
    router = APIRouter()

    class RunRequest(BaseModel):
        dataset: str
        model: str | None = None
        mode: str = "online"
        limit: int | None = None

    @router.post("/runs")
    async def start_run(req: RunRequest, bg: BackgroundTasks):
        """Start an evaluation run (async, returns run_id)."""
        from evals.engine.runner import EvalRunner
        from evals.storage.results import ResultStore

        dataset_path = Path("evals/datasets") / f"{req.dataset}.json"
        if not dataset_path.exists():
            raise HTTPException(404, f"dataset not found: {req.dataset}")
        dataset = json.loads(dataset_path.read_text())

        runner = EvalRunner()
        store = ResultStore()

        # Generate run_id first
        opts = {"model": req.model, "mode": req.mode}
        run_id = runner._make_run_id(dataset)
        opts["run_id"] = run_id

        # Run in background
        def _run():
            report = runner.run_dataset(dataset, opts)
            store.save(report)

        bg.add_task(_run)
        return {"run_id": run_id, "status": "started"}

    @router.get("/runs")
    async def list_runs():
        """List all evaluation runs."""
        from evals.storage.results import ResultStore
        store = ResultStore()
        runs = store.list_runs()
        return {"runs": runs}

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str):
        """Get a run's details + scorecard."""
        from evals.storage.results import ResultStore
        store = ResultStore()
        report = store.load(run_id)
        if not report:
            raise HTTPException(404, f"run not found: {run_id}")
        return report

    @router.get("/runs/{run_id}/results/{task_id}")
    async def get_task_result(run_id: str, task_id: str):
        """Get a single task's trace."""
        trace_path = Path("evals/results") / run_id / "traces" / f"{task_id}__0.json"
        if not trace_path.exists():
            raise HTTPException(404, "trace not found")
        return json.loads(trace_path.read_text())

    @router.post("/compare")
    async def compare_runs(run_a: str, run_b: str):
        """Compare two runs."""
        from evals.storage.results import ResultStore
        store = ResultStore()
        a = store.load(run_a)
        b = store.load(run_b)
        if not a or not b:
            raise HTTPException(404, "one or both runs not found")
        return {
            "run_a": {"pass_rate": a.get("judge_pass_rate"), "mean_score": a.get("judge_mean_score")},
            "run_b": {"pass_rate": b.get("judge_pass_rate"), "mean_score": b.get("judge_mean_score")},
        }
