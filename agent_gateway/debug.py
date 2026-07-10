"""agent_gateway.debug — optional DEBUG-level logging for the conversation flow.

Two separate concerns:
  1. Normal INFO-level operational logs under agent_gateway.* always print. We
     attach a stderr handler to the `agent_gateway` logger at INFO level once
     (uvicorn's default dictConfig only configures uvicorn.* loggers and leaves
     the root at WARNING, so without this our module INFO logs are swallowed).
  2. The conversation-flow instrumentation lives at DEBUG level and is gated by
     the AGENT_DEBUG env var (1/true/yes/on). When enabled, the
     `agent_gateway.debug` logger is lowered to DEBUG so those records reach the
     handler; when disabled, `debug()` short-circuits before any formatting.

Toggle: set AGENT_DEBUG=1 in .env / docker-compose env and restart the gateway.
INFO logs print either way; only DEBUG depends on the switch.

Coverage (the full conversation flow, prefix [DBG]):
  WS connect / connection.ack          (web_connect._handle_connection)
  inbound req method + sid             (web_connect._receiver)
  outbound res ok/error                (web_connect._receiver)
  _ensure_drain / _ensure_drain_live   (web_connect — bind/early-return/start_seq)
  drain start / replay / live / exit   (web_connect._drain_session)
  history.get received/emitted/done    (agent_compat._emit_history_stream)
  chat.send posted                     (agent_compat.execute_agent_request)
  worker → pipe emit (token/done/...)  (sessions.PipeSink.emit)
  turn lifecycle post/start/end        (sessions.post_message / _run_turn)
"""
from __future__ import annotations
import logging
import os

_ENABLED = os.environ.get("AGENT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

# Parent logger for all agent_gateway.* — give it a stderr handler at INFO so
# normal operational INFO logs print regardless of uvicorn's dictConfig.
_ag = logging.getLogger("agent_gateway")
if not _ag.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    _ag.addHandler(_h)
_ag.setLevel(logging.INFO)
_ag.propagate = False

# DEBUG-level instrumentation logger. Lowered to DEBUG only when AGENT_DEBUG is
# on; records propagate to the parent handler above.
_logger = logging.getLogger("agent_gateway.debug")
if _ENABLED:
    _logger.setLevel(logging.DEBUG)


def is_enabled() -> bool:
    return _ENABLED


def debug(fmt: str, *args) -> None:
    """DEBUG-level log. fmt is %-style; args are substituted only when enabled
    (so disabled-state pays no formatting cost)."""
    if _ENABLED:
        try:
            _logger.debug("[DBG] " + (fmt % args if args else fmt))
        except Exception:
            _logger.debug("[DBG] %s", fmt)
