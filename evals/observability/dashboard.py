"""evals.observability.dashboard — real-time monitoring dashboard.

LiveScorecardCalculator + DashboardService + GET /eval/obs/dashboard.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Query

from evals.observability.router import ObservabilityContext


class LiveScorecardCalculator:
    """Compute a live scorecard from completed task results."""

    def calculate(self, per_task_status: dict, completed_results: list) -> dict:
        try:
            total = len(per_task_status)
            ok = sum(1 for v in per_task_status.values() if v.get("status") == "ok")
            error = sum(1 for v in per_task_status.values()
                        if v.get("status") in ("error", "timeout"))
            pass_rate = (ok / total) if total > 0 else 0.0
            return {
                "completed": total,
                "ok": ok,
                "error": error,
                "pass_rate": pass_rate,
            }
        except Exception:
            return {}


class DashboardService:
    """Aggregate active run views for the dashboard."""

    def __init__(self, ctx: ObservabilityContext):
        self.ctx = ctx
        self._calc = LiveScorecardCalculator()

    def build_view(self, run_id: Optional[str] = None) -> dict:
        rm = self.ctx.run_manager
        if rm is None:
            return {"runs": []}

        runs = []
        with rm._lock:
            handles = list(rm._runs.values())
        for h in handles:
            if run_id and h.run_id != run_id:
                continue
            elapsed_ms = int((time.time() - h.started_at) * 1000)
            completed = sum(1 for v in h.per_task_status.values()
                            if v.get("status") in ("ok", "error", "timeout", "skipped"))
            total = getattr(h, "total_tasks", 0) or len(h.progress)
            avg_task_ms = (elapsed_ms / completed) if completed > 0 else 0
            estimated_remaining_ms = int(avg_task_ms * max(0, total - completed))
            live_scorecard = self._calc.calculate(h.per_task_status, h.progress)
            task_details = [
                {"task_id": tid, "status": v.get("status", ""),
                 "duration_ms": v.get("duration_ms", 0), "rep": v.get("rep", 0)}
                for tid, v in h.per_task_status.items()
            ]
            runs.append({
                "run_id": h.run_id,
                "dataset": h.dataset,
                "model": h.model,
                "mode": "",
                "started_at": h.started_at,
                "status": h.status,
                "completed_tasks": completed,
                "total_tasks": total,
                "current_task_id": h.current_task_id,
                "elapsed_ms": elapsed_ms,
                "estimated_remaining_ms": estimated_remaining_ms,
                "live_scorecard": live_scorecard,
                "task_details": task_details,
            })
        return {"runs": runs}


def create_router(ctx: ObservabilityContext) -> APIRouter:
    router = APIRouter()
    svc = DashboardService(ctx)

    @router.get("/dashboard")
    async def dashboard(run_id: Optional[str] = Query(None)):
        try:
            return svc.build_view(run_id)
        except Exception:
            return {"runs": []}

    return router
