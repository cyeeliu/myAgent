"""REST routes for evaluation data + control plane.

Data plane (large payloads: traces, reports) is served directly here.
Control plane (start / list / get / cancel / compare runs) is provided by
``evals.api`` and merged into this router under the same ``/api/eval`` prefix
(E-F9), so the full REST eval interface is live whenever the gateway runs.
"""
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


# E-F9: merge the eval control-plane router (start/list/get/cancel/compare).
# evals.api.router has no prefix of its own, so it inherits this router's
# /api/eval prefix without path collisions (control-plane endpoints differ
# from the data-plane endpoints above).
try:
    from evals.api import router as _eval_control_router
    router.include_router(_eval_control_router)
except Exception:  # pragma: no cover — evals.api requires fastapi; gateway has it
    pass
