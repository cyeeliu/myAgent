"""Pydantic schemas for the gateway: request bodies + event frame envelope.

Event kinds reuse the agent core's enum (token, text, tool_start, tool_result,
error, permission_request, compacted, done) — no parallel taxonomy.
"""
from pydantic import BaseModel, Field
from typing import Any, Optional


# ── Client → server request bodies ──

class CreateSession(BaseModel):
    transport: str = "auto"  # ws | sse | auto


class UserMessage(BaseModel):
    text: str


class PermissionResponse(BaseModel):
    allow: bool
    modify: Optional[str] = None


# ── Server → client event frame (WS json / SSE data) ──

class EventFrame(BaseModel):
    seq: int
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
