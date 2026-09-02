"""E2A envelope — the channel-agnostic wrapper carried through the gateway.

An envelope ties a request to its routing identity (channel_id, session_id,
request_id) so downstream code never re-derives it from params. The envelope
is normalized (`gateway_normalize`) into an AgentRequest before execution.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional

from ..schema.message import ReqMethod
from ..schema.agent import PermissionContext


@dataclass
class E2AEnvelope:
    channel_id: str                        # "web" | "feishu" | …
    method: ReqMethod
    params: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    session_id: Optional[str] = None
    permission: Optional[PermissionContext] = None
    # raw inbound frame kept for diagnostics / legacy field extraction
    raw: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "method": self.method.value,
            "params": self.params,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "permission": self.permission.to_dict() if self.permission else None,
        }
