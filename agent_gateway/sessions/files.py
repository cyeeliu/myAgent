"""Session file writing + content stringification.

Persists the conversation to ``agent/sessions/{sid}/`` as a readable
``transcript.md`` and a raw ``history.json`` for the SessionsPanel file browser.
"""
from __future__ import annotations

import json
import time
from typing import Any

from agent_core import _block_type, _block_attr

from ._constants import (
    SESSION_FILES_ROOT,
    CONTINUATION_PROMPT,
    TODO_REMINDER_PREFIX,
    TASK_NOTIFICATION_PREFIX,
)


def _is_internal_user_prompt(text: str) -> bool:
    """True if this user message is an agent-internal nudge, not a real turn."""
    return (text == CONTINUATION_PROMPT
            or text.startswith("[Compacted.")
            or text.startswith(TODO_REMINDER_PREFIX)
            or text.startswith(TASK_NOTIFICATION_PREFIX))


def _stringify(content: Any) -> str:
    """Flatten a message content (str or list of blocks) to a plain string.

    Blocks may be dicts (hydrated from DB/JSON) or SimpleNamespace instances
    (_TextBlock/_ToolUseBlock) when the session is live in memory, so use
    _block_type/_block_attr instead of assuming dict shape."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if _block_type(b) == "text":
                parts.append(_block_attr(b, "text", "") or "")
        return "".join(parts)
    return str(content)


def _write_session_files(sid: str, record: list) -> None:
    """Persist the conversation to ``agent/sessions/{sid}/`` as a readable
    transcript.md and a raw history.json so the SessionsPanel file browser
    (which browses that dir via /file-api) has previewable content. Best-effort:
    failures are swallowed (the DB is the source of truth, not these files).

    history.json is written in the shape the myagent SessionsPanel preview
    parser (parseHistoryTimelineEntry) expects: each user turn as
    {role:"user", content:<str>, timestamp}, each assistant text as
    {role:"assistant", event_type:"chat.final", content:<str>, timestamp}.
    The raw agent_core record stores assistant content as a list of blocks with
    no event_type, which the parser drops (normalizeFinalContent returns '' for
    non-string content) — so the preview would show only user messages. We
    flatten blocks to a string here."""
    try:
        out = SESSION_FILES_ROOT / sid
        out.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        history: list[dict] = []
        base_ts = time.time()
        idx = 0
        for msg in record:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content")
            if role == "user":
                text = _stringify(content)
                if not text.strip():
                    continue
                if _is_internal_user_prompt(text):
                    continue  # agent-internal nudge, not a real user turn
                lines.append(f"## 🧑 User\n\n{text}\n")
                history.append({"role": "user", "content": text,
                                "timestamp": base_ts + idx})
                idx += 1
            elif role == "assistant":
                text = _stringify(content)
                if not text.strip():
                    continue
                lines.append(f"## 🤖 Assistant\n\n{text}\n")
                history.append({"role": "assistant", "event_type": "chat.final",
                                "content": text, "timestamp": base_ts + idx})
                idx += 1
        (out / "transcript.md").write_text("\n".join(lines) or "(empty session)", encoding="utf-8")
        (out / "history.json").write_text(
            json.dumps(history, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
    except Exception:
        pass
