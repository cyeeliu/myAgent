"""agent_core.tracing — lightweight span-based tracing / observability.

Provides a thread-safe tracer that records spans (LLM calls, tool calls,
turns) with timing, token counts, and status.  Spans form a tree via
parent_id.  The tracer is a singleton; spans are stored in-memory and
optionally exported as JSON for /api/metrics or OTLP.

Usage in loop.py / adapter.py:
    from agent_core.tracing import tracer
    span = tracer.start_span("llm_call", {"model": model_id})
    ... do work ...
    tracer.end_span(span, {"tokens_in": 100, "tokens_out": 200})
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from typing import Any

_MAX_SPANS = 5000  # ring buffer cap


@dataclass
class Span:
    """A single tracing span."""
    id: str
    name: str
    parent_id: str | None = None
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # ok / error / cancelled
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class Tracer:
    """Thread-safe span tracer with in-memory ring buffer."""

    def __init__(self):
        self._spans: deque[Span] = deque(maxlen=_MAX_SPANS)
        self._lock = threading.Lock()
        self._active: dict[str, Span] = {}  # span_id → Span (in-flight)
        self._counters: dict[str, int] = defaultdict(int)
        self._token_totals: dict[str, int] = defaultdict(int)

    def start_span(self, name: str, attributes: dict | None = None,
                   parent_id: str | None = None) -> Span:
        """Start a new span. Returns the span handle."""
        span = Span(
            id=uuid.uuid4().hex[:16],
            name=name,
            parent_id=parent_id,
            start_time=time.time(),
            attributes=attributes or {},
        )
        with self._lock:
            self._active[span.id] = span
            self._counters[f"{name}.started"] += 1
        return span

    def end_span(self, span: Span, attributes: dict | None = None,
                 status: str = "ok", error: str = "") -> None:
        """End a span, recording duration and final attributes."""
        span.end_time = time.time()
        span.duration_ms = (span.end_time - span.start_time) * 1000
        span.status = status
        span.error = error
        if attributes:
            span.attributes.update(attributes)

        with self._lock:
            self._active.pop(span.id, None)
            self._spans.append(span)
            self._counters[f"{span.name}.completed"] += 1
            if status == "error":
                self._counters[f"{span.name}.errors"] += 1
            # Track token totals
            tokens_in = span.attributes.get("tokens_in", 0)
            tokens_out = span.attributes.get("tokens_out", 0)
            if tokens_in:
                self._token_totals["tokens_in"] += tokens_in
            if tokens_out:
                self._token_totals["tokens_out"] += tokens_out

    def record_event(self, name: str, attributes: dict | None = None) -> None:
        """Record a point-in-time event (not a span)."""
        with self._lock:
            self._counters[name] += 1

    def get_metrics(self) -> dict:
        """Return aggregate metrics for /api/metrics."""
        with self._lock:
            spans_list = list(self._spans)
            # Compute per-name stats
            by_name: dict[str, dict] = {}
            for s in spans_list:
                if s.name not in by_name:
                    by_name[s.name] = {
                        "count": 0, "errors": 0,
                        "total_ms": 0.0, "min_ms": float("inf"), "max_ms": 0.0,
                    }
                stats = by_name[s.name]
                stats["count"] += 1
                stats["total_ms"] += s.duration_ms
                stats["min_ms"] = min(stats["min_ms"], s.duration_ms)
                stats["max_ms"] = max(stats["max_ms"], s.duration_ms)
                if s.status == "error":
                    stats["errors"] += 1

            for name, stats in by_name.items():
                if stats["count"] > 0:
                    stats["avg_ms"] = stats["total_ms"] / stats["count"]
                    stats["error_rate"] = stats["errors"] / stats["count"]
                if stats["min_ms"] == float("inf"):
                    stats["min_ms"] = 0.0

            return {
                "uptime_spans": len(spans_list),
                "active_spans": len(self._active),
                "counters": dict(self._counters),
                "token_totals": dict(self._token_totals),
                "by_span": by_name,
            }

    def get_recent_spans(self, limit: int = 100) -> list[dict]:
        """Return the most recent spans as dicts."""
        with self._lock:
            spans = list(self._spans)[-limit:]
            return [s.to_dict() for s in reversed(spans)]

    def get_trace_tree(self, root_span_id: str) -> dict | None:
        """Build a tree from a root span."""
        with self._lock:
            spans_by_id = {s.id: s for s in self._spans}
            root = spans_by_id.get(root_span_id)
            if root is None:
                return None

            def build(node: Span) -> dict:
                children = [build(s) for s in self._spans
                           if s.parent_id == node.id]
                return {
                    **node.to_dict(),
                    "children": children,
                }
            return build(root)

    def clear(self) -> None:
        """Clear all spans and counters."""
        with self._lock:
            self._spans.clear()
            self._active.clear()
            self._counters.clear()
            self._token_totals.clear()


# Singleton
tracer = Tracer()


# ── Context manager helper ──

class SpanContext:
    """Context manager for span lifecycle."""
    def __init__(self, name: str, attributes: dict | None = None,
                 parent_id: str | None = None):
        self.name = name
        self.attributes = attributes
        self.parent_id = parent_id
        self.span: Span | None = None

    def __enter__(self) -> Span:
        self.span = tracer.start_span(self.name, self.attributes, self.parent_id)
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            tracer.end_span(self.span, status="error",
                           error=f"{exc_type.__name__}: {exc_val}")
        else:
            tracer.end_span(self.span)
        return False  # don't suppress exceptions


def span(name: str, attributes: dict | None = None, parent_id: str | None = None):
    """Decorator/context-manager for tracing a function or block."""
    return SpanContext(name, attributes, parent_id)
