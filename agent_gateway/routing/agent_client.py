"""AgentServerClient — in-process client for agent_core (mirrors jiuwenswarm routing/agent_client).

In jiuwenswarm this is a WS client to a remote AgentServer. myAgent keeps
agent_core in-process, so the "client" is a thin async wrapper around
agent_compat.execute_agent_request + the SessionManager. The interface is kept
WS-client-shaped (request/subscribe) so swapping to a real remote AgentServer
later touches only this file.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional

from ..common.schema.agent import AgentRequest, AgentResponse
from ..common.e2a.agent_compat import execute_agent_request

logger = logging.getLogger(__name__)


class AgentServerClient:
    def __init__(self, sessions):
        self.sessions = sessions

    async def request(self, req: AgentRequest, *, timeout_ms: Optional[int] = None) -> AgentResponse:
        if timeout_ms is not None:
            try:
                return await asyncio.wait_for(
                    execute_agent_request(req, sessions=self.sessions),
                    timeout=timeout_ms / 1000.0)
            except asyncio.TimeoutError:
                return AgentResponse(request_id=req.request_id, ok=False,
                                     error="agent request timeout")
        return await execute_agent_request(req, sessions=self.sessions)

    async def subscribe_events(self, session_id: str, on_event):
        """No-op: in-process events flow through the session EventPipe directly
        to the WebChannel drain. Kept for interface parity with a remote client."""
        return None
