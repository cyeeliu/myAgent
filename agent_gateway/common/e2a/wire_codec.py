"""Wire codec — encode/decode the E2A envelope to/from the WS wire frame.

Wire shapes (single `/ws` connection, myagent-style):
  req   : {type:"req",   id, method, params}
  res   : {type:"res",   id, ok, payload, error?}
  event : {type:"event", event, payload, seq?, stream_id?}

The codec is the only place that knows the wire layout — swapping transports
(WS→gRPC, adding a second AgentServer process) touches only here.
"""
from __future__ import annotations
import json
from typing import Any, Optional

from .models import E2AEnvelope
from ..schema.message import ReqMethod
from ..schema.agent import AgentResponse


def encode_envelope(env: E2AEnvelope) -> dict[str, Any]:
    """Envelope → outbound req frame (rarely needed in-process; used for logging/fanout)."""
    return {"type": "req", "id": env.request_id, "method": env.method.value,
            "params": env.params}


def decode_envelope(raw: dict[str, Any], channel_id: str = "web") -> Optional[E2AEnvelope]:
    """Inbound wire frame → Envelope. Returns None if the frame isn't a req."""
    if not isinstance(raw, dict) or raw.get("type") != "req":
        return None
    method_name = raw.get("method")
    method = ReqMethod.from_str(method_name) if isinstance(method_name, str) else None
    if method is None:
        return None
    params = raw.get("params")
    if not isinstance(params, dict):
        params = {}
    return E2AEnvelope(
        channel_id=channel_id,
        method=method,
        params=params,
        request_id=str(raw.get("id") or ""),
        session_id=params.get("session_id") if isinstance(params.get("session_id"), str) else None,
        raw=raw,
    )


def _json_safe(obj: Any) -> Any:
    """Coerce agent_core block objects (SimpleNamespace-based _TextBlock /
    _ToolUseBlock, and nested dicts/lists) into JSON-serializable plain dicts.
    agent_core stores chat records as messages whose `content` is a list of these
    blocks; without this, ws.send_json raises "Object of type _TextBlock is not
    JSON serializable" on history.get / session payloads that include the record.
    """
    from types import SimpleNamespace
    if isinstance(obj, SimpleNamespace):
        d = dict(vars(obj))
        t = getattr(type(obj), "type", None)
        if t and "type" not in d:
            d["type"] = t
        return {k: _json_safe(v) for k, v in d.items()}
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def encode_response(resp: AgentResponse) -> dict[str, Any]:
    d = resp.to_dict()
    d["payload"] = _json_safe(d.get("payload"))
    return d


def encode_error(request_id: str, message: str, code: Optional[str] = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "res", "id": request_id, "ok": False,
                         "payload": {}, "error": message}
    if code:
        d["code"] = code
    return d


def encode_event(event: str, payload: dict[str, Any], seq: Optional[int] = None,
                 stream_id: Optional[str] = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "event", "event": event, "payload": payload}
    if seq is not None:
        d["seq"] = seq
    if stream_id:
        d["stream_id"] = stream_id
    return d


def parse_frame(text: str) -> Optional[dict[str, Any]]:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None
