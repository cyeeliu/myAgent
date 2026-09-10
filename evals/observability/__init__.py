"""evals.observability — observability subsystem for the evals framework.

Provides structured logging, metric export, real-time dashboard, event stream,
alerting, trace visualization, trend analysis, and health checks — all mounted
under a unified ``/eval/obs`` prefix.
"""
from __future__ import annotations

from evals.observability.router import ObservabilityRouter

__all__ = ["ObservabilityRouter"]
