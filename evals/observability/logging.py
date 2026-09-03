"""evals.observability.logging — structured logging subsystem.

JsonLogFormatter, RedactingFilter, FileAndStdoutHandler, EvalLogger.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter

from evals.observability import config
from evals.observability.router import ObservabilityContext

# ── Context variables (thread-local via contextvars) ──
_ctx_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("eval_run_id", default="")
_ctx_task_id: contextvars.ContextVar[str] = contextvars.ContextVar("eval_task_id", default="")
_ctx_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("eval_trace_id", default="")


# ── 2.1 JsonLogFormatter ──

class JsonLogFormatter(logging.Formatter):
    """Structured JSON log formatter with eval-specific fields.

    Extends ``agent_gateway.logging_config.JsonFormatter`` with
    ``timestamp``/``event_type``/``run_id``/``task_id``/``trace_id``/``extra``.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "event_type": getattr(record, "event_type", ""),
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", "") or _ctx_run_id.get(""),
            "task_id": getattr(record, "task_id", "") or _ctx_task_id.get(""),
            "trace_id": getattr(record, "trace_id", "") or _ctx_trace_id.get(""),
            "extra": {},
        }
        if record.exc_info:
            payload["extra"]["exc"] = self.formatException(record.exc_info)

        extra_data = getattr(record, "extra_data", None)
        if extra_data and isinstance(extra_data, dict):
            payload["extra"].update(extra_data)

        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            payload["extra"] = str(payload.get("extra", ""))
            try:
                return json.dumps(payload, ensure_ascii=False, default=str)
            except Exception:
                return json.dumps({"timestamp": payload["timestamp"],
                                    "level": payload["level"],
                                    "message": payload["message"]}, ensure_ascii=False)


# ── 2.2 RedactingFilter ──

_REDACT_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{6,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9\-_.~+/]+", re.IGNORECASE),
]
_REDACT_FIELD_RE = re.compile(
    r"(api_key|apikey|token|password|secret|authorization)",
    re.IGNORECASE,
)
_REDACTED = "***REDACTED***"


class RedactingFilter(logging.Filter):
    """Redact API keys, bearer tokens, and sensitive fields from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat in _REDACT_PATTERNS:
            msg = pat.sub(_REDACTED, msg)
        record.msg = msg
        record.args = ()

        extra_data = getattr(record, "extra_data", None)
        if extra_data and isinstance(extra_data, dict):
            record.extra_data = {k: _REDACTED if _REDACT_FIELD_RE.search(k) else v
                                 for k, v in extra_data.items()}
        return True


# ── 2.3 FileAndStdoutHandler ──

class FileAndStdoutHandler(logging.Handler):
    """Write logs to a per-run file and optionally stdout.

    File path is anchored at ``REPO_ROOT/evals/results/<run_id>/logs/eval.log``
    — independent of the process working directory.
    """

    def __init__(self, dual_output: bool | None = None):
        super().__init__()
        self._dual = config.EVAL_LOG_DUAL_OUTPUT if dual_output is None else dual_output
        self._file: Optional[logging.FileHandler] = None
        self._current_run_id: str = ""
        self._lock = __import__("threading").Lock()
        self._degraded = False

    def _ensure_file(self, run_id: str):
        if run_id == self._current_run_id and self._file is not None:
            return
        with self._lock:
            if run_id == self._current_run_id and self._file is not None:
                return
            if self._file is not None:
                try:
                    self._file.close()
                except Exception:
                    pass
            self._current_run_id = run_id
            if not run_id:
                self._file = None
                return
            try:
                log_dir = config.EVAL_LOG_DIR / run_id / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                self._file = logging.FileHandler(str(log_dir / "eval.log"), encoding="utf-8")
                self._degraded = False
            except Exception:
                self._file = None
                self._degraded = True
                if self._dual:
                    sys.stderr.write(f"[eval-log] file write degraded for run {run_id}, stdout only\n")

    def emit(self, record: logging.LogRecord):
        run_id = getattr(record, "run_id", "") or _ctx_run_id.get("")
        self._ensure_file(run_id)
        msg = self.format(record)
        if self._file is not None:
            try:
                self._file.emit(record)
            except Exception:
                self._file = None
                self._degraded = True
        if self._dual:
            try:
                sys.stderr.write(msg + "\n")
            except Exception:
                pass

    def close(self):
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None
        super().close()


# ── 2.4 EvalLogger ──

class EvalLogger:
    """Wrapper around ``logging.Logger`` with eval context injection.

    Provides ``emit(level, event_type, message, extra)`` and context
    management via ``bind_context`` / ``clear_context``.
    """

    _instances: dict[str, "EvalLogger"] = {}
    _instance_lock = __import__("threading").Lock()

    def __init__(self, name: str = "evals.observability"):
        self._logger = logging.getLogger(name)
        self._logger.setLevel(getattr(logging, config.EVAL_LOG_LEVEL, logging.INFO))
        if not self._logger.handlers:
            handler = FileAndStdoutHandler()
            handler.setFormatter(JsonLogFormatter())
            handler.addFilter(RedactingFilter())
            self._logger.addHandler(handler)
        self._logger.propagate = False

    @classmethod
    def get(cls, name: str = "evals.observability") -> "EvalLogger":
        with cls._instance_lock:
            if name not in cls._instances:
                cls._instances[name] = cls(name)
            return cls._instances[name]

    def bind_context(self, run_id: str = "", task_id: str = "", trace_id: str = ""):
        return _EvalContext(run_id, task_id, trace_id)

    def clear_context(self):
        _ctx_run_id.set("")
        _ctx_task_id.set("")
        _ctx_trace_id.set("")

    def emit(self, level: str, event_type: str, message: str, extra: dict | None = None):
        try:
            log_level = getattr(logging, level.upper(), logging.INFO)
            self._logger.log(
                log_level,
                message,
                extra={
                    "event_type": event_type,
                    "run_id": _ctx_run_id.get(""),
                    "task_id": _ctx_task_id.get(""),
                    "trace_id": _ctx_trace_id.get(""),
                    "extra_data": extra or {},
                },
            )
        except Exception:
            pass


class _EvalContext:
    """Context manager for binding eval context."""

    def __init__(self, run_id: str, task_id: str, trace_id: str):
        self._run_id = run_id
        self._task_id = task_id
        self._trace_id = trace_id
        self._tokens: list = []

    def __enter__(self):
        self._tokens.append(_ctx_run_id.set(self._run_id))
        self._tokens.append(_ctx_task_id.set(self._task_id))
        self._tokens.append(_ctx_trace_id.set(self._trace_id))
        return self

    def __exit__(self, *exc):
        for var, token in zip([_ctx_run_id, _ctx_task_id, _ctx_trace_id], reversed(self._tokens)):
            try:
                var.reset(token)
            except Exception:
                pass
        return False


def get_eval_logger() -> EvalLogger:
    """Return the singleton EvalLogger instance."""
    return EvalLogger.get()


# ── Router (no HTTP endpoints for logging, but kept for uniformity) ──

def create_router(ctx: ObservabilityContext) -> APIRouter:
    router = APIRouter()
    return router
