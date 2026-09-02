"""agent_gateway.common.e2a — External-to-Agent envelope + wire codec.

Mirrors myagent's `common/e2a/`. A channel request (web WS, IM, …) is
wrapped in an E2AEnvelope, normalized to an AgentRequest, then executed
in-process against agent_core (single-process — no separate AgentServer WS).
"""
from .models import E2AEnvelope
from .wire_codec import (
    encode_envelope, decode_envelope, encode_response, encode_error,
    encode_event, parse_frame,
)
from .gateway_normalize import e2a_from_channel_request
from .agent_compat import execute_agent_request

__all__ = [
    "E2AEnvelope",
    "encode_envelope", "decode_envelope", "encode_response", "encode_error",
    "encode_event", "parse_frame",
    "e2a_from_channel_request",
    "execute_agent_request",
]
