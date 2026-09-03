"""evals.observability.metrics_exporter — metric export (Prometheus / JSON).

MetricsExporter, PrometheusFormatter, MetricsJsonFormatter, GET /eval/obs/metrics.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, JSONResponse

from evals.observability.router import ObservabilityContext


class MetricsExporter:
    """Collect metric snapshots from active runs and history."""

    def __init__(self, ctx: ObservabilityContext):
        self.ctx = ctx

    def collect(self, run_id: Optional[str] = None) -> dict:
        try:
            from evals.metrics.base import list_metrics
            meta = list_metrics()
        except Exception:
            meta = {}

        snapshot: dict[str, dict] = {}
        rm = self.ctx.run_manager
        if rm is not None:
            with rm._lock:
                handles = list(rm._runs.values())
            for h in handles:
                if run_id and h.run_id != run_id:
                    continue
                if h.scorecard:
                    for name, val in h.scorecard.items():
                        if name.startswith("_"):
                            continue
                        numeric = val
                        if isinstance(val, dict):
                            numeric = val.get("mean", 0.0)
                        m = meta.get(name, {})
                        snapshot[name] = {
                            "value": numeric,
                            "description": m.get("description", ""),
                            "direction": m.get("direction", ""),
                            "run_id": h.run_id,
                        }

        if run_id and not snapshot:
            try:
                report = rm.get_run(run_id) if rm else None
                if report:
                    sc = report.get("scorecard", {})
                    for name, val in sc.items():
                        if name.startswith("_"):
                            continue
                        numeric = val
                        if isinstance(val, dict):
                            numeric = val.get("mean", 0.0)
                        m = meta.get(name, {})
                        snapshot[name] = {
                            "value": numeric,
                            "description": m.get("description", ""),
                            "direction": m.get("direction", ""),
                            "run_id": run_id,
                        }
            except Exception:
                pass
        return snapshot


class PrometheusFormatter:
    @staticmethod
    def format(snapshot: dict) -> str:
        lines = []
        for name, info in snapshot.items():
            val = info.get("value")
            if not isinstance(val, (int, float)):
                continue
            prom_name = name.replace(".", "_").replace("-", "_")
            desc = info.get("description", "")
            lines.append(f"# HELP {prom_name} {desc}")
            lines.append(f"# TYPE {prom_name} gauge")
            rid = info.get("run_id", "")
            lines.append(f'{prom_name}{{run_id="{rid}"}} {val}')
        return "\n".join(lines) + ("\n" if lines else "")


class MetricsJsonFormatter:
    @staticmethod
    def format(snapshot: dict) -> dict:
        result: dict[str, dict] = {}
        for name, info in snapshot.items():
            result[name] = {
                "value": info.get("value"),
                "description": info.get("description", ""),
                "direction": info.get("direction", ""),
            }
            if "run_id" in info:
                result[name]["run_id"] = info["run_id"]
        return result


def create_router(ctx: ObservabilityContext) -> APIRouter:
    router = APIRouter()
    exporter = MetricsExporter(ctx)

    @router.get("/metrics")
    async def metrics(request: Request, format: Optional[str] = Query(None),
                      run_id: Optional[str] = Query(None)):
        snapshot = exporter.collect(run_id)
        accept = request.headers.get("accept", "")
        use_prom = format == "prometheus" or (format is None and "text/plain" in accept)
        if use_prom:
            return PlainTextResponse(PrometheusFormatter.format(snapshot))
        return JSONResponse(MetricsJsonFormatter.format(snapshot))

    return router
