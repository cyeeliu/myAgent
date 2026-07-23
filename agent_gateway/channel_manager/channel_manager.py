"""ChannelManager — channel lifecycle + outbound dispatch loop (mirrors myagent).

Owns:
  1. channel register/unregister/find
  2. the outbound dispatch loop (drains MessageHandler's outbound queue and
     fans each frame to the channel it's addressed to)
  3. a background task per channel for start/stop

Inbound routing goes through MessageHandler (set via set_message_handler);
outbound comes from MessageHandler.subscribe_outbound.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

from .base import BaseChannel

logger = logging.getLogger(__name__)


class ChannelManager:
    def __init__(self):
        self._channels: dict[str, BaseChannel] = {}
        self._message_handler = None
        self._dispatch_task: Optional[asyncio.Task] = None
        self._running = False

    def set_message_handler(self, handler) -> None:
        self._message_handler = handler

    def register(self, channel: BaseChannel) -> None:
        self._channels[channel.channel_id] = channel
        logger.info("registered channel %s (%s)", channel.channel_id,
                    channel.metadata.channel_type)

    def unregister(self, channel_id: str) -> None:
        self._channels.pop(channel_id, None)

    def get(self, channel_id: str) -> Optional[BaseChannel]:
        return self._channels.get(channel_id)

    def all(self) -> list[BaseChannel]:
        return list(self._channels.values())

    async def start_all(self) -> None:
        if self._running:
            return
        self._running = True
        for ch in self._channels.values():
            try:
                await ch.start()
            except Exception:
                logger.exception("failed to start channel %s", ch.channel_id)
        if self._message_handler is not None:
            self._dispatch_task = asyncio.create_task(self._outbound_loop(),
                                                      name="channel-outbound")

    async def stop_all(self) -> None:
        self._running = False
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except (asyncio.CancelledError, Exception):
                pass
            self._dispatch_task = None
        for ch in self._channels.values():
            try:
                await ch.stop()
            except Exception:
                logger.exception("failed to stop channel %s", ch.channel_id)

    async def _outbound_loop(self) -> None:
        """Drain MessageHandler.outbound and deliver to the addressed channel.

        In single-process myAgent the outbound path is the session EventPipe →
        WebChannel subscriber (wired directly in web_connect), so this loop is a
        no-op fallback for channels that opt into the centralized queue. Kept to
        mirror myagent's double-queue shape for future IM channels.
        """
        if self._message_handler is None:
            return
        outbound = getattr(self._message_handler, "outbound_queue", None)
        if outbound is None:
            return
        while self._running:
            try:
                item = await outbound.get()
            except asyncio.CancelledError:
                break
            channel_id = item.get("channel_id")
            frame = item.get("frame")
            ch = self._channels.get(channel_id) if channel_id else None
            if ch is not None and frame is not None:
                try:
                    await ch.send_event(frame)
                except Exception:
                    logger.exception("outbound dispatch failed for %s", channel_id)
            outbound.task_done()
