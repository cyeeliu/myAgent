"""Unified logging configuration for the gateway.

Centralizes the logging setup that was previously split between ``debug.py``
(ad-hoc handler attachment) and implicit uvicorn defaults. Called once during
app startup via ``setup_logging()``.

Three log tiers:
  1. ``agent_gateway``      — INFO-level operational logs (always on).
  2. ``agent_gateway.debug`` — DEBUG-level flow instrumentation (AGENT_DEBUG=1).
  3. ``uvicorn`` / ``uvicorn.access`` — server logs (left to uvicorn's config).

When ``LOG_FORMAT=json`` is set, records are emitted as structured JSON lines
suitable for log aggregation (ELK, Loki, Datadog).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter for log aggregation pipelines."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Attach extra fields if present
        for key in ("request_id", "session_id", "method"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(*, debug: bool | None = None, log_level: str | None = None) -> None:
    """Configure the ``agent_gateway`` logger hierarchy.

    Idempotent — safe to call multiple times (removes existing handlers first).

    Args:
        debug:   If True, lower ``agent_gateway.debug`` to DEBUG. Defaults to
                 the ``AGENT_DEBUG`` env var.
        log_level: Root level for ``agent_gateway``. Defaults to ``LOG_LEVEL``
                   env var or ``INFO``.
    """
    if debug is None:
        debug = os.environ.get("AGENT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    if log_level is None:
        log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    use_json = os.environ.get("LOG_FORMAT", "").strip().lower() == "json"

    # Parent logger for all agent_gateway.*
    ag = logging.getLogger("agent_gateway")
    ag.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    if use_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    ag.addHandler(handler)
    ag.setLevel(getattr(logging, log_level, logging.INFO))
    ag.propagate = False

    # DEBUG instrumentation logger
    dbg = logging.getLogger("agent_gateway.debug")
    if debug:
        dbg.setLevel(logging.DEBUG)
    else:
        dbg.setLevel(logging.INFO)
