"""Agent request/response models (mirrors myagent common/schema/agent).

`AgentRequest` is the normalized form every channel request becomes after e2a
normalization; `agent_compat.execute` turns it into an in-process agent_core
call (single-process — no WS hop to a separate AgentServer). `PermissionContext`
carries the identity/scene info agent_core's permission hook needs.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .message import ReqMethod, Mode


class PermissionContext:
    """Identity + scene for permission decisions.

    principal_user_id: the owner (channel config's my_user_id).
    triggering_user_id: who triggered the action (IM sender / web user).
    channel_id: which channel ("web", "feishu", …).
    """

    __slots__ = ("principal_user_id", "triggering_user_id", "channel_id", "web_user_id")

    def __init__(self, principal_user_id: str = "", triggering_user_id: str = "",
                 channel_id: str = "", web_user_id: str = ""):
        self.principal_user_id = principal_user_id
        self.triggering_user_id = triggering_user_id
        self.channel_id = channel_id
        self.web_user_id = web_user_id

    @property
    def scene(self) -> str:
        if self.channel_id == "web":
            return "web"
        return "normal_im"

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal_user_id": self.principal_user_id,
            "triggering_user_id": self.triggering_user_id,
            "channel_id": self.channel_id,
            "web_user_id": self.web_user_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PermissionContext":
        return cls(
            principal_user_id=data.get("principal_user_id", ""),
            triggering_user_id=data.get("triggering_user_id", ""),
            channel_id=data.get("channel_id", ""),
            web_user_id=data.get("web_user_id", ""),
        )


@dataclass
class AgentRequest:
    """Normalized request handed to agent_compat.execute.

    For single-process myAgent, `method` decides which agent_core path to take;
    `params` is the raw method params; `session_id` binds to a GatewaySession.
    """
    method: ReqMethod
    params: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    session_id: Optional[str] = None
    channel_id: str = "web"
    mode: Optional[Mode] = None
    permission: Optional[PermissionContext] = None
    # internal metadata bag (evolution approval, plan approval, …) — passthrough
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method.value,
            "params": self.params,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "channel_id": self.channel_id,
            "mode": self.mode.value if self.mode else None,
            "permission": self.permission.to_dict() if self.permission else None,
            "meta": self.meta,
        }


@dataclass
class AgentResponse:
    """Final (non-streaming) response from agent_core."""
    request_id: str
    ok: bool = True
    payload: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "res", "id": self.request_id, "ok": self.ok,
                             "payload": self.payload}
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class AgentResponseChunk:
    """One streaming chunk pushed to the channel during a turn."""
    request_id: str
    event: str
    payload: dict[str, Any] = field(default_factory=dict)
    seq: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "event", "event": self.event,
                             "payload": self.payload}
        if self.seq is not None:
            d["seq"] = self.seq
        return d
