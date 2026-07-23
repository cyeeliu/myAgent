"""HeartbeatService — periodic liveness + optional relay to channels.

Emits a `heartbeat.relay` event every `interval` seconds that channels can
forward to clients (the WebChannel already sends its own WS heartbeat; this
service is for IM/monitoring channels and a /api/health pulse). Mirrors
myagent's heartbeat service, scaled down to single-process.
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class HeartbeatService:
    def __init__(self, interval: float = 30.0, on_tick=None):
        self.interval = interval
        self.on_tick = on_tick
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.last_beat: float = 0.0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="heartbeat")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.interval)
            self.last_beat = time.time()
            if self.on_tick is not None:
                try:
                    await self.on_tick({"t": self.last_beat, "status": "ok"})
                except Exception:
                    logger.exception("heartbeat on_tick failed")
