"""evals.observability.config — centralized configuration for the observability subsystem.

All settings support environment-variable override with sensible defaults.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from agent_core.paths import REPO_ROOT
except Exception:
    REPO_ROOT = Path.cwd()


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


EVAL_OBS_PREFIX = os.environ.get("EVAL_OBS_PREFIX", "/eval/obs").rstrip("/")
EVAL_LOG_LEVEL = os.environ.get("EVAL_LOG_LEVEL", "INFO").upper()
EVAL_LOG_DUAL_OUTPUT = _env_bool("EVAL_LOG_DUAL_OUTPUT", True)
EVAL_ALERT_RULES_PATH = Path(
    os.environ.get("EVAL_ALERT_RULES_PATH", str(REPO_ROOT / "evals" / "alerts" / "rules.yaml"))
)
EVAL_EVENT_SSE_HEARTBEAT = _env_float("EVAL_EVENT_SSE_HEARTBEAT", 15.0)
EVAL_METRICS_EXPORT_TIMEOUT_MS = _env_int("EVAL_METRICS_EXPORT_TIMEOUT_MS", 200)
EVAL_LOG_DIR = REPO_ROOT / "evals" / "results"
