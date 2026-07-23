"""gateway_normalize — channel request → AgentRequest (mirrors myagent e2a).

Pulls session_id / mode / permission out of the method params into first-class
AgentRequest fields so agent_compat.execute doesn't re-parse. Keeps the raw
params intact for the handler.
"""
from __future__ import annotations
from typing import Any, Optional

from ..schema.message import ReqMethod, Mode
from ..schema.agent import AgentRequest, PermissionContext
from .models import E2AEnvelope


def _coerce_mode(value: Any) -> Optional[Mode]:
    if value is None:
        return None
    if isinstance(value, Mode):
        return value
    s = str(value).strip().lower()
    for m in Mode:
        if m.value == s:
            return m
    return None


def e2a_from_channel_request(env: E2AEnvelope) -> AgentRequest:
    """Normalize an envelope into an AgentRequest ready for agent_compat.execute."""
    params = env.params
    perm = env.permission
    if perm is None and env.channel_id:
        perm = PermissionContext(channel_id=env.channel_id)

    session_id = env.session_id or (params.get("session_id")
                                    if isinstance(params.get("session_id"), str) else None)
    mode = _coerce_mode(params.get("mode"))

    # Carry evolution/plan-approval metadata through untouched.
    meta: dict[str, Any] = {}
    for k in ("evolution_meta", "plan_approval_kind", "plan_content", "plan_language",
              "approval_schema", "source", "new_input", "intent"):
        if k in params:
            meta[k] = params[k]

    return AgentRequest(
        method=env.method,
        params=params,
        request_id=env.request_id,
        session_id=session_id,
        channel_id=env.channel_id,
        mode=mode,
        permission=perm,
        meta=meta,
    )
