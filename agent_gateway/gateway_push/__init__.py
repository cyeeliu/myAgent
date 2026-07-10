"""gateway_push — agent_core events → wire event frames (mirrors jiuwenswarm gateway_push).

agent_core emits short-named events (token, tool_start, tool_result, …). The wire
layer maps them to jiuwenswarm-style dotted event names at the boundary so the
frontend speaks the same protocol as OpenJiuwen Swarm. This is the ONLY place
that knows both taxonomies.
"""
from .wire import frame_to_event, kind_to_event, build_event
from .transport import EventPusher

__all__ = ["frame_to_event", "kind_to_event", "build_event", "EventPusher"]
