"""agent_gateway.channel_manager — multi-channel abstraction (mirrors jiuwenswarm).

A Channel is a transport (web WS, IM, ACP, …) that produces inbound requests
and consumes outbound events. ChannelManager owns registration, the outbound
dispatch loop, and routes inbound through MessageHandler. Today only WebChannel
is wired; the abstraction exists so IM/ACP can be added without touching core.
"""
from .base import BaseChannel, ChannelMetadata, RobotMessageRouter
from .channel_manager import ChannelManager

__all__ = ["BaseChannel", "ChannelMetadata", "RobotMessageRouter", "ChannelManager"]
