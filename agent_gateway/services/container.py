"""Service container and startup wiring for the gateway.

All service instantiation (MessageHandler, ChannelManager, WebChannel,
HeartbeatService, marketplace registrations) lives here, assembled once in
``create_app()``. The container owns start/stop lifecycle so the app lifespan
stays thin.
"""
from __future__ import annotations

import asyncio
import logging
import os

from agent_gateway.debug import debug
from agent_gateway.sessions import manager
from agent_gateway import db, pipe as pipe_mod
from agent_gateway.channel_manager import ChannelManager
from agent_gateway.channel_manager.web import WebChannel, WebChannelConfig, register_web_handlers
from agent_gateway.message_handler import MessageHandler
from agent_gateway.heartbeat import HeartbeatService

_log = logging.getLogger(__name__)


class ServiceContainer:
    """Owns all gateway services and their lifecycle.

    Created once in ``create_app()``. Services that need async start
    (MessageHandler loop, Heartbeat) are started in ``start()`` and stopped in
    ``stop()``. The WebChannel route is mounted on the FastAPI app via
    ``mount_routes()``.
    """

    def __init__(self):
        self.manager = manager

        # ── myagent-style wiring ──
        self.message_handler = MessageHandler(manager)
        self.channel_manager = ChannelManager()
        self.channel_manager.set_message_handler(self.message_handler)

        self.web_channel = WebChannel(WebChannelConfig(
            enabled=True,
            host=os.environ.get("GATEWAY_HOST", "0.0.0.0"),
            port=int(os.environ.get("GATEWAY_PORT", "8000")),
            path="/ws",
        ), sessions=manager, message_handler=self.message_handler)
        register_web_handlers(self.web_channel, manager)
        self.channel_manager.register(self.web_channel)

        self.heartbeat = HeartbeatService(interval=30.0)

        # ── marketplace installer registration ──
        self._register_marketplaces()

    def _register_marketplaces(self) -> None:
        """Register marketplace installers so the agent's download_skill tool
        can pull from clawhub/skillhub/skillnet/teamskills by source + id."""
        from agent_gateway import skill_marketplaces as mp
        from agent_core.skills import (
            register_marketplace_installer,
            register_marketplace_search,
        )
        register_marketplace_installer("clawhub", mp.clawhub_download)
        register_marketplace_installer("skillhub", mp.skillhub_install)
        register_marketplace_installer("skillnet", mp.skillnet_install)
        register_marketplace_installer("teamskills", mp.teamskills_install)
        register_marketplace_search(mp.search_marketplaces)

    def mount_routes(self, app) -> None:
        """Mount the WS route on the FastAPI app (called after app creation)."""
        self.web_channel.mount(app)

    async def start(self) -> None:
        """Async startup: init DB/Redis pools, start background services."""
        debug("gateway starting")
        # Seed per-workspace skills + agent-data.json on startup (best-effort).
        try:
            from agent_gateway.services.agent_data import rebuild_agent_data
            await asyncio.to_thread(rebuild_agent_data)
        except Exception as exc:  # noqa: BLE001
            debug("startup rebuild_agent_data failed: %s", exc)

        db.init_pool(os.environ.get("DATABASE_URL"))
        pipe_mod.init_redis(os.environ.get("REDIS_URL"))
        await self.message_handler.start()
        await self.channel_manager.start_all()
        await self.heartbeat.start()

    async def stop(self) -> None:
        """Async shutdown: stop services + close pools."""
        await self.heartbeat.stop()
        await self.channel_manager.stop_all()
        await self.message_handler.stop()
        await pipe_mod.close_redis()
        db.close_pool()
