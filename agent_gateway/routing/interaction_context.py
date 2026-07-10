"""InteractionContext — per-request context carried through hooks/handlers.

Mirrors jiuwenswarm routing/interaction_context. Holds the channel, session,
request_id, and permission context for the duration of one inbound request so
hook handlers and approval coordinators don't re-derive it.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional

from ..common.schema.agent import PermissionContext


@dataclass
class InteractionContext:
    request_id: str
    channel_id: str
    session_id: Optional[str] = None
    permission: Optional[PermissionContext] = None
    extra: dict[str, Any] = field(default_factory=dict)
