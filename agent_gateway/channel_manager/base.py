"""BaseChannel + ChannelMetadata + RobotMessageRouter (mirrors myagent base.py).

A channel registers method handlers (`register_method`) for inbound requests and
publishes outbound events via `send_event`. The ChannelManager drives the
outbound dispatch loop; inbound requests are routed through RobotMessageRouter
→ MessageHandler → agent_compat.
"""
from __future__ import annotations
import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Method handler signature: (params, channel, request_id) -> Any (payload) or AgentResponse.
MethodHandler = Callable[..., Awaitable[Any]]
# Outbound event subscriber.
EventSubscriber = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class ChannelMetadata:
    channel_id: str
    channel_type: str           # "web" | "feishu" | …
    enabled: bool = True
    host: str = ""
    port: int = 0
    path: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class BaseChannel(ABC):
    """A transport channel.

    Subclasses (WebChannel, IM channels, …) implement start/stop and the
    inbound wire parsing. Method routing is generic and lives here.
    """

    def __init__(self, metadata: ChannelMetadata):
        self.metadata = metadata
        self._method_handlers: dict[str, MethodHandler] = {}
        self._event_subscribers: list[EventSubscriber] = []
        self._started = False

    @property
    def channel_id(self) -> str:
        return self.metadata.channel_id

    # ── method routing ──
    def register_method(self, method: str, handler: MethodHandler) -> None:
        self._method_handlers[method] = handler

    def has_method(self, method: str) -> bool:
        return method in self._method_handlers

    async def dispatch_method(self, method: str, params: dict[str, Any],
                              request_id: str = "") -> Any:
        handler = self._method_handlers.get(method)
        if handler is None:
            raise KeyError(f"no handler for method {method}")
        return await handler(params, self, request_id)

    # ── outbound events ──
    def subscribe_events(self, sub: EventSubscriber) -> None:
        self._event_subscribers.append(sub)

    def unsubscribe_events(self, sub: EventSubscriber) -> None:
        try:
            self._event_subscribers.remove(sub)
        except ValueError:
            pass

    async def send_event(self, frame: dict[str, Any]) -> None:
        """Fan an event frame to every subscriber (usually one WS connection)."""
        for sub in list(self._event_subscribers):
            try:
                await sub(frame)
            except Exception:
                logger.exception("event subscriber failed on channel %s", self.channel_id)

    # ── lifecycle ──
    @abstractmethod
    async def start(self) -> None: ...

    async def stop(self) -> None:
        self._started = False

    @property
    def started(self) -> bool:
        return self._started


class RobotMessageRouter:
    """Routes inbound channel messages to the MessageHandler.

    Inbound: channel receives a req → router.handle(channel, envelope) →
    MessageHandler.enqueue_inbound. Outbound: MessageHandler emits events →
    channel.send_event. Kept as a thin indirection so channels don't depend
    on MessageHandler's concrete shape.
    """

    def __init__(self, message_handler):
        self._handler = message_handler

    async def handle(self, channel: BaseChannel, envelope) -> Any:
        return await self._handler.handle_inbound(channel, envelope)
