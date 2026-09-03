"""evals.observability.trend — metric history trend and degradation detection.

TrendService, GET /eval/obs/trend.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query

from evals.observability.router import ObservabilityContext


class TrendService:
    """Compute metric trends with degradation direction."""

    def __init__(self, ctx: ObservabilityContext):
        self.ctx = ctx

    def query(self, dataset: str, metrics: list[str],
              since: Optional[float] = None, until: Optional[float] = None,
              limit: int = 50) -> list[dict]:
        rm = self.ctx.run_manager
        if rm is None:
            return []
        try:
            from evals.metrics.base import list_metrics
            meta = list_metrics()
        except Exception:
            meta = {}

        points: list[dict] = []
        try:
            for run_id in self._list_runs(rm):
                report = rm.get_run(run_id)
                if not report or report.get("dataset") != dataset:
                    continue
                ts = report.get("started_at", 0)
                if since and ts < since:
                    continue
                if until and ts > until:
                    continue
                sc = report.get("scorecard", {})
                entry: dict[str, Any] = {
                    "run_id": run_id,
                    "timestamp": ts,
                }
                for m in metrics:
                    val = sc.get(m)
                    if isinstance(val, dict):
                        val = val.get("mean", 0.0)
                    entry[m] = val
                entry["changes"] = {}
                points.append(entry)
        except Exception:
            pass

        points.sort(key=lambda p: p["timestamp"])
        points = points[:limit]

        for i in range(1, len(points)):
            for m in metrics:
                prev = points[i - 1].get(m)
                curr = points[i].get(m)
                if prev is None or curr is None:
                    continue
                direction = meta.get(m, {}).get("direction", "↑")
                if curr > prev:
                    degraded = direction == "↓"
                elif curr < prev:
                    degraded = direction == "↑"
                else:
                    points[i]["changes"][m] = "flat"
                    continue
                points[i]["changes"][m] = "degraded" if degraded else "improved"
        return points

    def _list_runs(self, rm) -> list[str]:
        try:
            from evals.storage.results import ResultStore
            return ResultStore().list_runs()
        except Exception:
            return []


def create_router(ctx: ObservabilityContext) -> APIRouter:
    router = APIRouter()
    svc = TrendService(ctx)

    @router.get("/trend")
    async def trend(dataset: str = Query(...),
                    metrics: str = Query(...),
                    since: Optional[float] = Query(None),
                    until: Optional[float] = Query(None),
                    limit: int = Query(50)):
        metric_list = [m.strip() for m in metrics.split(",") if m.strip()]
        return svc.query(dataset, metric_list, since, until, limit)

    return router
