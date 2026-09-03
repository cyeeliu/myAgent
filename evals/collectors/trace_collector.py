"""evals.collectors.trace_collector — span-level data from the tracer singleton.

Reads from agent_core.tracing.tracer (a module-level singleton) to get
latency, token, and error span data. Read-only, zero intrusion.
"""
from __future__ import annotations

from typing import Any


class TraceCollector:
    """Read span aggregates from the tracer singleton."""

    def snapshot(self, limit: int = 10000) -> list[dict]:
        """Get recent spans as a list of dicts."""
        try:
            from agent_core.tracing import tracer
            spans = tracer.get_recent_spans(limit=limit)
            return [
                {
                    "name": s.name if hasattr(s, "name") else s.get("name", ""),
                    "duration_ms": s.duration_ms if hasattr(s, "duration_ms") else s.get("duration_ms", 0),
                    "status": s.status if hasattr(s, "status") else s.get("status", "ok"),
                    "tokens_in": s.tokens_in if hasattr(s, "tokens_in") else s.get("tokens_in", 0),
                    "tokens_out": s.tokens_out if hasattr(s, "tokens_out") else s.get("tokens_out", 0),
                }
                for s in spans
            ]
        except (ImportError, AttributeError):
            return []

    def aggregate(self) -> dict:
        """Get aggregated metrics from the tracer."""
        try:
            from agent_core.tracing import tracer
            return tracer.get_metrics()
        except (ImportError, AttributeError):
            return {}

    def merge_into(self, trace) -> None:
        """Merge span data into an existing EvalTrace."""
        trace.spans = self.snapshot()
        # Enrich meta with token totals
        agg = self.aggregate()
        if agg:
            trace.meta.setdefault("token_totals", agg.get("token_totals", {}))
            trace.meta.setdefault("span_count", len(trace.spans))
