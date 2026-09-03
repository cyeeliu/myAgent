"""REST routes for evaluation data plane (large payloads: traces, reports)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from agent_gateway.services.eval_run_manager import get_eval_runs

router = APIRouter(prefix="/api/eval", tags=["eval"])


@router.get("/runs/{run_id}/results/{task_id}")
async def get_task_trace(run_id: str, task_id: str):
    """Get a single task's full trace (potentially large, hence REST not WS)."""
    mgr = get_eval_runs()
    trace = mgr.load_trace(run_id, task_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return JSONResponse(content=trace)


@router.get("/runs/{run_id}/report")
async def get_report(run_id: str, format: str = "json"):
    """Download a full report (json)."""
    mgr = get_eval_runs()
    report = mgr.get_run(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    if format == "json":
        return JSONResponse(content=report)
    raise HTTPException(status_code=400, detail="unsupported format")
