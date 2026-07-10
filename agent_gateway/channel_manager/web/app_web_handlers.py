"""WebChannel RPC handlers — register one handler per ReqMethod (mirrors jiuwenswarm app_web_handlers).

Each handler signature: async def(params, channel, request_id) -> payload dict.
The handler runs through agent_compat.execute_agent_request, which is the single
dispatch point. This module is where you'd add gateway-side concerns that aren't
agent_core (rate limiting, audit, hook fanout) — for now it's a thin registration
that points every method at agent_compat so the WebChannel can dispatch without
importing agent_core itself.

The WebChannel already calls execute_agent_request directly in its receiver loop
(the single-process fast path). These handlers exist so other channels (future
IM/ACP) and the ChannelManager can dispatch the same methods through the
BaseChannel.dispatch_method interface, and so tests can call them in isolation.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any

from ...common.schema.message import ReqMethod
from ...common.schema.agent import AgentRequest, AgentResponse
from ...common.e2a.gateway_normalize import e2a_from_channel_request
from ...common.e2a.agent_compat import execute_agent_request
from ...common.e2a.models import E2AEnvelope

logger = logging.getLogger(__name__)


def register_web_handlers(channel, sessions) -> None:
    """Register a handler for every ReqMethod on the WebChannel."""

    async def _handler(params: dict[str, Any], ch, request_id: str) -> Any:
        env = E2AEnvelope(
            channel_id=ch.channel_id,
            method=ReqMethod.from_str(_handler_method) or ReqMethod.CHAT_SEND,
            params=params,
            request_id=request_id,
            session_id=params.get("session_id") if isinstance(params.get("session_id"), str) else None,
        )
        req = e2a_from_channel_request(env)
        resp = await execute_agent_request(req, sessions=sessions)
        return resp.payload if resp.ok else {"error": resp.error}

    for method in ReqMethod:
        # Each method needs its own bound handler; use a factory to capture the name.
        channel.register_method(method.value, _make_handler(method, sessions))


def _make_handler(method: ReqMethod, sessions):
    async def handler(params: dict[str, Any], ch, request_id: str) -> Any:
        env = E2AEnvelope(
            channel_id=ch.channel_id,
            method=method,
            params=params,
            request_id=request_id,
            session_id=params.get("session_id") if isinstance(params.get("session_id"), str) else None,
        )
        req = e2a_from_channel_request(env)
        resp = await execute_agent_request(req, sessions=sessions)
        if not resp.ok:
            return {"error": resp.error}
        return resp.payload
    return handler
