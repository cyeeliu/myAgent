"""evals.observability.trace_viz — trace visualization (span tree).

SpanIndex, TraceTreeBuilder, GET /eval/obs/trace.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

from fastapi import APIRouter, Query

from evals.observability.router import ObservabilityContext


class SpanIndex:
    """Maintain run_id+task_id → root_span_id mapping."""

    def __init__(self):
        self._index: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def register(self, run_id: str, task_id: str, root_span_id: str):
        with self._lock:
            self._index[(run_id, task_id)] = root_span_id

    def lookup(self, run_id: str, task_id: str) -> Optional[str]:
        with self._lock:
            return self._index.get((run_id, task_id))

    def clear(self, run_id: str):
        with self._lock:
            keys = [k for k in self._index if k[0] == run_id]
            for k in keys:
                del self._index[k]


_span_index = SpanIndex()


def get_span_index() -> SpanIndex:
    return _span_index


class TraceTreeBuilder:
    """Build a nested span tree for visualization."""

    def build(self, run_id: str, task_id: str) -> dict:
        root_id = _span_index.lookup(run_id, task_id)
        if not root_id:
            return {"id": "", "name": "", "children": []}
        try:
            from agent_core.tracing import Tracer
            tracer = Tracer()
            tree = tracer.get_trace_tree(root_id)
            if tree:
                return self._convert(tree)
        except Exception:
            pass
        return {"id": root_id, "name": task_id, "children": []}

    def _convert(self, node: dict) -> dict:
        return {
            "id": node.get("id", ""),
            "name": node.get("name", ""),
            "parent_id": node.get("parent_id", ""),
            "start_time": node.get("start_time", 0),
            "end_time": node.get("end_time", 0),
            "duration_ms": node.get("duration_ms", 0),
            "status": node.get("status", "ok"),
            "error": node.get("error", ""),
            "tokens_in": node.get("tokens_in", 0),
            "tokens_out": node.get("tokens_out", 0),
            "children": [self._convert(c) for c in node.get("children", [])],
        }


def create_router(ctx: ObservabilityContext) -> APIRouter:
    router = APIRouter()
    builder = TraceTreeBuilder()

    @router.get("/trace")
    async def trace(run_id: str = Query(...), task_id: str = Query(...)):
        return builder.build(run_id, task_id)

    return router
