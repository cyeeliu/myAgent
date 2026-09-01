"""agent_compat — AgentRequest → in-process agent_core call (single-process).

In myagent this would be a WS hop to a separate AgentServer. myAgent keeps
agent_core in-process, so "compat" is a direct dispatch via the method registry.

This module is the ONLY place that imports `code` (agent_core) for request
execution, keeping the agent_core boundary clean. The actual per-method logic
lives in ``handlers/`` (one module per domain); this file just wires the
registry and provides the ``execute_agent_request`` entry point.
"""
from __future__ import annotations

import asyncio

from ..schema.agent import AgentRequest, AgentResponse
from .dispatcher import registry, HandlerContext

# Importing the handlers package registers every @handler-decorated function
# on the module-level ``registry`` singleton. This must happen before any
# request is dispatched.
from . import handlers  # noqa: F401 — side-effect import (handler registration)


async def execute_agent_request(req: AgentRequest, *, sessions) -> AgentResponse:
    """Dispatch a normalized AgentRequest against agent_core in-process.

    `sessions` is the SessionManager (agent_gateway.sessions.manager). Methods
    that touch a live session resolve it via sessions.get_or_hydrate; chat.send
    posts to the worker thread and returns immediately (events stream back over
    the pipe). Returns an AgentResponse (ok/error + payload).
    """
    loop = asyncio.get_running_loop()
    ctx = HandlerContext(sessions=sessions, loop=loop)
    return await registry.dispatch(req, ctx)
