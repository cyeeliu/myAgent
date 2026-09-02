"""WebChannel — browser WebSocket channel (mirrors myagent web_connect)."""
from .web_connect import WebChannel, WebChannelConfig
from .app_web_handlers import register_web_handlers

__all__ = ["WebChannel", "WebChannelConfig", "register_web_handlers"]
