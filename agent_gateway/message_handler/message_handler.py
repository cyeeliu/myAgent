"""MessageHandler — double-queue inbound routing (mirrors jiuwenswarm message_handler).

Inbound queue: channel reqs → handler → agent_compat (in-process).
Outbound queue: agent_core events addressed to a channel → channel.send_event.

In single-process myAgent the outbound path is wired directly (WebChannel drains
the session EventPipe), so the outbound_queue here is an optional fallback for
future IM channels. The inbound path is the canonical route for non-web channels
and for tests; the WebChannel fast-path calls agent_compat directly to avoid
queueing latency for the browser.

The double-queue shape is kept so adding backpressure / prioritization later
touches only this file.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional

from ..common.e2a.gateway_normalize import e2a_from_channel_request
from ..common.e2a.agent_compat import execute_agent_request
from ..common.schema.agent import AgentResponse
from ..routing.session_map import SessionMap

logger = logging.getLogger(__name__)


class MessageHandler:
    def __init__(self, sessions):
        self.sessions = sessions
        self.session_map = SessionMap()
        self.inbound_queue: asyncio.Queue = asyncio.Queue()
        self.outbound_queue: asyncio.Queue = asyncio.Queue()
        self._inbound_task: Optional[asyncio.Task] = None
        self._running = False

    async def handle_inbound(self, channel, envelope) -> AgentResponse:
        """Synchronous (inline) inbound handling — used by channels that want a
        direct response (web). Returns the AgentResponse to send back as `res`."""
        req = e2a_from_channel_request(envelope)
        try:
            return await execute_agent_request(req, sessions=self.sessions)
        except Exception as e:
            logger.exception("inbound handling failed for %s", envelope.method.value)
            return AgentResponse(request_id=envelope.request_id, ok=False,
                                 error=f"{type(e).__name__}: {e}")

    async def enqueue_inbound(self, channel, envelope) -> None:
        """Async queue path — for channels that don't need an inline response
        (IM). The inbound loop drains and executes."""
        await self.inbound_queue.put((channel, envelope))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._inbound_task = asyncio.create_task(self._inbound_loop(),
                                                 name="message-handler-inbound")

    async def stop(self) -> None:
        self._running = False
        if self._inbound_task is not None:
            self._inbound_task.cancel()
            try:
                await self._inbound_task
            except (asyncio.CancelledError, Exception):
                pass
            self._inbound_task = None

    async def _inbound_loop(self) -> None:
        while self._running:
            try:
                channel, envelope = await self.inbound_queue.get()
            except asyncio.CancelledError:
                break
            try:
                resp = await self.handle_inbound(channel, envelope)
                # For queued (non-web) channels, push the response as an event.
                if resp is not None and channel is not None:
                    await self.outbound_queue.put({
                        "channel_id": channel.channel_id,
                        "frame": resp.to_dict(),
                    })
            except Exception:
                logger.exception("inbound loop error")
            self.inbound_queue.task_done()
