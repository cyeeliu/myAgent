"""Replay frame synthesis from the append-only chat record.

Rebuilds token-level replay frames so a freshly hydrated session can replay its
FULL conversation. Used when the live EventPipe has expired (>24h) and the
session is re-seeded from the durable chat record.
"""
from __future__ import annotations

from agent_core import _block_type, _block_attr, todo_payload

from .files import _stringify, _is_internal_user_prompt


def _last_todos_from_record(record: list) -> list:
    """Scan the chat record for the last todo_write tool_use and return its
    raw `todos` input (or [] if none). Used to repopulate Session.todos on
    hydrate so has_active_todo() stays correct across eviction/restart, and
    the nudge logic doesn't lose track of unfinished todos."""
    last: list | None = None
    for msg in record or []:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if _block_type(b) == "tool_use" and _block_attr(b, "name") == "todo_write":
                inp = _block_attr(b, "input", {})
                tlist = inp.get("todos") if isinstance(inp, dict) else None
                if isinstance(tlist, list):
                    last = tlist
    return last or []


def synthesize_frames(record: list) -> list[dict]:
    """Rebuild replay frames from the append-only chat record so a freshly
    hydrated session can replay its FULL conversation. Returns frames with
    seq 1..N; the caller seeds them into the live pipe and advances agent._seq
    to N. Reads from `record` (never compacted), not the LLM context."""
    seq = 0
    frames: list[dict] = []
    last_todos: list | None = None  # reconstructed from the last todo_write tool_use
    for msg in record:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            if isinstance(content, str):
                if _is_internal_user_prompt(content):
                    continue  # internal prompt, not a real user turn
                seq += 1
                frames.append({"seq": seq, "kind": "user", "payload": {"text": content, "seq": seq}})
            elif isinstance(content, list):
                for b in content:
                    if _block_type(b) == "tool_result":
                        seq += 1
                        frames.append({"seq": seq, "kind": "tool_result",
                                       "payload": {"id": _block_attr(b, "tool_use_id") or _block_attr(b, "id"),
                                                   "content": _stringify(_block_attr(b, "content")),
                                                   "blocked": bool(_block_attr(b, "is_error")),
                                                   "seq": seq}})
        elif role == "assistant":
            if isinstance(content, str):
                seq += 1
                frames.append({"seq": seq, "kind": "token", "payload": {"text": content, "seq": seq}})
            elif isinstance(content, list):
                for b in content:
                    bt = _block_type(b)
                    if bt == "text":
                        seq += 1
                        frames.append({"seq": seq, "kind": "token",
                                       "payload": {"text": _block_attr(b, "text", ""), "seq": seq}})
                    elif bt == "tool_use":
                        seq += 1
                        frames.append({"seq": seq, "kind": "tool_start",
                                       "payload": {"id": _block_attr(b, "id"),
                                                   "name": _block_attr(b, "name"),
                                                   "input": _block_attr(b, "input", {}), "seq": seq}})
                        # Track the last todo_write so a reconnect whose live
                        # stream expired (>24h, replay synthesized from the
                        # chat record) still repopulates the TodoList panel.
                        if _block_attr(b, "name") == "todo_write":
                            inp = _block_attr(b, "input", {})
                            tlist = inp.get("todos") if isinstance(inp, dict) else None
                            if isinstance(tlist, list):
                                last_todos = tlist
                        # Re-emit show_widget artifacts positionally so a
                        # synthesized (>24h) reconnect still renders the SVG/HTML
                        # widget the agent produced. Within 24h the original live
                        # `widget` frame is replayed from the EventPipe.
                        if _block_attr(b, "name") == "show_widget":
                            inp = _block_attr(b, "input", {})
                            if isinstance(inp, dict) and inp.get("content"):
                                seq += 1
                                frames.append({"seq": seq, "kind": "widget",
                                               "payload": {**inp, "seq": seq}})
    # Re-emit the final todo state as the last replay frame so the panel
    # restores on a synthesized (>24h) reconnect. Within 24h the original
    # live `todo` frame is replayed from the EventPipe and this is redundant
    # (harmless — setTodos is idempotent).
    if last_todos is not None:
        seq += 1
        frames.append({"seq": seq, "kind": "todo",
                       "payload": {"todos": todo_payload(last_todos), "seq": seq}})
    return frames
