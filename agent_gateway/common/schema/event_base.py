"""Event base — the common shape of every server→client push.

A gateway push is `{event, payload, seq?}`. `event` is a dotted name
(`chat.delta`, `chat.tool_call`, `session.updated`, …) matching myagent's
wire. Concrete event kinds are produced by gateway_push.wire from agent_core's
EVENT_KINDS, so there is no parallel taxonomy: agent_core keeps its short
names, the wire layer maps them at the boundary.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class EventBase:
    event: str
    payload: dict[str, Any] = field(default_factory=dict)
    seq: Optional[int] = None          # monotonic per-session seq for resume/dedup
    stream_id: Optional[str] = None    # groups chunks of one streaming response

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "event", "event": self.event, "payload": self.payload}
        if self.seq is not None:
            d["seq"] = self.seq
        if self.stream_id is not None:
            d["stream_id"] = self.stream_id
        return d
