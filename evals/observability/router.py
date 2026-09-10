"""evals.observability.router — unified entry point for all observability endpoints.

Aggregates eight sub-module routers under a single ``/eval/obs`` prefix and
provides dependency injection for ``EvalRunManager`` and ``ResultStore``.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter

from evals.observability import config


class ObservabilityRouter:
    """Aggregate all observability sub-routers under one prefix."""

    def __init__(self):
        self.prefix = config.EVAL_OBS_PREFIX
        self.router = APIRouter(prefix=self.prefix, tags=["eval-observability"])
        self._run_manager: Any = None
        self._result_store: Any = None
        self._initialized = False

    def init(self, run_manager: Any, result_store: Any) -> "ObservabilityRouter":
        """Inject dependencies and mount all sub-routers.

        Idempotent — calling again replaces the previous mount context.
        """
        self._run_manager = run_manager
        self._result_store = result_store
        self._initialized = True

        from evals.observability.logging import create_router as _logging
        from evals.observability.metrics_exporter import create_router as _metrics
        from evals.observability.dashboard import create_router as _dashboard
        from evals.observability.event_stream import create_router as _events
        from evals.observability.alerts import create_router as _alerts
        from evals.observability.trace_viz import create_router as _trace
        from evals.observability.health import create_router as _health
        from evals.observability.trend import create_router as _trend

        ctx = ObservabilityContext(run_manager, result_store)

        self.router.include_router(_logging(ctx))
        self.router.include_router(_metrics(ctx))
        self.router.include_router(_dashboard(ctx))
        self.router.include_router(_events(ctx))
        self.router.include_router(_alerts(ctx))
        self.router.include_router(_trace(ctx))
        self.router.include_router(_health(ctx))
        self.router.include_router(_trend(ctx))
        return self

    @property
    def run_manager(self) -> Any:
        return self._run_manager

    @property
    def result_store(self) -> Any:
        return self._result_store


class ObservabilityContext:
    """Shared context object passed to each sub-module's ``create_router``."""

    def __init__(self, run_manager: Any, result_store: Any):
        self.run_manager = run_manager
        self.result_store = result_store
