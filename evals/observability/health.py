"""evals.observability.health — liveness / readiness probes.

GET /eval/obs/health/live, GET /eval/obs/health/ready.
"""
from __future__ import annotations

import os
import tempfile

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from evals.observability.router import ObservabilityContext


def create_router(ctx: ObservabilityContext) -> APIRouter:
    router = APIRouter()

    @router.get("/health/live")
    async def liveness():
        return JSONResponse({"status": "ok"}, status_code=200)

    @router.get("/health/ready")
    async def readiness():
        try:
            store = ctx.result_store
            if store is not None:
                base = getattr(store, "base_dir", None)
                if base:
                    test_file = base / ".health_check"
                    test_file.parent.mkdir(parents=True, exist_ok=True)
                    test_file.write_text("ok")
                    test_file.unlink()
            return JSONResponse({"status": "ready"}, status_code=200)
        except Exception:
            return JSONResponse({"status": "not ready"}, status_code=503)

    return router
