"""wire — map agent_core event frames → myagent-style dotted event frames.

agent_core EVENT_KINDS: token, text, tool_start, tool_result, error,
permission_request, compacted, done, user, task_notification, memory.
Wire event names: chat.delta, chat.notice, chat.tool_call, chat.tool_result,
chat.error, chat.ask_user_question, chat.compacted, chat.final, chat.user,
chat.task_notification, chat.memory.

`frame_to_event` takes a pipe frame `{seq, kind, payload}` and returns a wire
dict `{type:"event", event, payload, seq}` (or None for kinds we don't forward).
"""
from __future__ import annotations
from typing import Any, Optional

# agent_core kind → wire event name
_KIND_MAP = {
    "token": "chat.delta",
    "text": "chat.notice",
    "tool_start": "chat.tool_call",
    "tool_start_delta": "chat.tool_call_delta",
    "tool_result": "chat.tool_result",
    "error": "chat.error",
    "permission_request": "chat.ask_user_question",
    "ask_user": "chat.ask_user_question",
    "widget": "chat.widget",
    "compacted": "chat.compacted",
    "done": "chat.final",
    "user": "chat.user",
    "task_notification": "chat.task_notification",
    "context_usage": "context.usage",
    "todo": "todo.updated",
    "memory": "chat.memory",
    "ping": "heartbeat",
    "history_message": "history.message",
    "team_member": "team.member",
    "team_task": "team.task",
    "team_event": "team.event",
    "team_message": "team.message",
    "eval_progress": "eval.progress",
    "eval_task_complete": "eval.task_complete",
    "eval_run_complete": "eval.run.complete",
    "eval_run_error": "eval.run.error",
}


def kind_to_event(kind: str) -> Optional[str]:
    return _KIND_MAP.get(kind)


def build_event(event: str, payload: dict[str, Any], seq: Optional[int] = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "event", "event": event, "payload": payload}
    if seq is not None:
        d["seq"] = seq
    return d


# tool_start → tool_result correlation: agent_core's tool_result frame carries only
# {id, content} (no tool name), but the myagent frontend's tool_result normalizer
# reads tool_name/name for display. Cache id→name from tool_start so the result can
# carry the name. Bounded; entries self-evict on the next tool_start with the same id.
_TOOL_NAME_CACHE: dict[str, str] = {}
_TOOL_NAME_CACHE_LIMIT = 256


def _remember_tool_name(tool_id: Any, name: Any) -> None:
    if not isinstance(tool_id, str) or not isinstance(name, str):
        return
    if len(_TOOL_NAME_CACHE) >= _TOOL_NAME_CACHE_LIMIT:
        _TOOL_NAME_CACHE.clear()
    _TOOL_NAME_CACHE[tool_id] = name


def _remap_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Map agent_core payload fields → myagent frontend-expected field names.

    The frontend's useWebSocket handlers read these keys (see toolEventNormalizer):
      chat.delta      → payload.content
      chat.tool_call  → payload.id / name / arguments (parseArguments accepts str|obj)
      chat.tool_result→ payload.id (toolCallId) / tool_name|name / result / success
      chat.final      → payload.content
    agent_core emits:
      token       {text}
      tool_start  {id, name, input}
      tool_result {id, content, blocked?}
      done        {} | {reason}
    """
    if kind == "token":
        return {"content": payload.get("text", "")}
    if kind == "tool_start":
        tool_id = payload.get("id")
        name = payload.get("name")
        _remember_tool_name(tool_id, name)
        args = payload.get("input")
        # arguments may be a dict; the frontend's parseArguments accepts both
        # JSON strings and objects, so pass through as-is.
        return {"id": tool_id, "name": name, "arguments": args}
    if kind == "tool_start_delta":
        # Partial tool_use delta — emitted during streaming so the frontend
        # can render tool arguments incrementally as they arrive.
        tool_id = payload.get("id")
        name = payload.get("name")
        if tool_id and name:
            _remember_tool_name(tool_id, name)
        return {
            "id": tool_id,
            "name": name,
            "arguments": payload.get("input_partial"),
            "partial": True,
            "index": payload.get("index"),
        }
    if kind == "tool_result":
        tool_id = payload.get("id")
        content = payload.get("content")
        name = _TOOL_NAME_CACHE.get(tool_id) if isinstance(tool_id, str) else None
        return {
            "id": tool_id,
            "tool_call_id": tool_id,
            "tool_name": name,
            "name": name,
            "result": content if isinstance(content, str) else (
                "" if content is None else str(content)
            ),
            "success": not payload.get("blocked") and not payload.get("error"),
        }
    if kind == "done":
        return {"content": payload.get("text", "")}
    if kind == "text":
        return {"content": payload.get("text", "")}
    if kind == "error":
        return {"error": payload.get("error") or payload.get("text", ""),
                "recoverable": bool(payload.get("recoverable", False))}
    # team.* events: payload is already {"event": {...}} (session_id tagged by
    # the WS drain). Pass through unchanged so the frontend's payload.event
    # reader finds the event object.
    if kind in ("team_member", "team_task", "team_event", "team_message"):
        return payload
    return payload


def frame_to_event(frame: dict[str, Any]) -> Optional[dict[str, Any]]:
    """pipe frame → wire event frame. Returns None for unmapped/ignored kinds."""
    if not isinstance(frame, dict):
        return None
    kind = frame.get("kind")
    event = kind_to_event(kind)
    if event is None:
        return None
    payload = frame.get("payload") or {}
    seq = frame.get("seq")
    # Normalize permission_request → ask_user_question payload shape.
    if kind == "permission_request":
        reason = payload.get("reason", "tool")
        detail = payload.get("detail", "")
        # Fold the detail (command / path) into the question body so the user
        # sees exactly what they are approving. The UserQuestionModal renders
        # question.question + question.options; it has no separate detail slot.
        body = f"{reason}: {detail}" if detail else reason
        payload = {
            "request_id": payload.get("request_id") or payload.get("rid") or payload.get("seq"),
            "questions": [{
                "header": "Permission",
                "question": body,
                "options": [
                    {"label": "Allow", "description": ""},
                    {"label": "Deny", "description": ""},
                ],
                "multi_select": False,
            }],
            "source": "permission_interrupt",
        }
    else:
        payload = _remap_payload(kind, payload)
    return build_event(event, payload, seq)
