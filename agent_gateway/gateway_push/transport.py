"""EventPusher — pushes wire event frames to a channel's send_event.

In single-process myAgent the WebChannel drains the session EventPipe directly
(see web_connect._drain_session), so this class is a thin helper for channels
that want a push-based interface (future IM channels) and for the MessageHandler
outbound queue path.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional

from .wire import build_event

logger = logging.getLogger(__name__)


class EventPusher:
    def __init__(self, channel):
        self._channel = channel

    async def push(self, event: str, payload: dict[str, Any],
                   seq: Optional[int] = None) -> None:
        frame = build_event(event, payload, seq)
        try:
            await self._channel.send_event(frame)
        except Exception:
            logger.exception("push failed to channel %s", self._channel.channel_id)
