"""Team event emission helpers and teammate event shim.

Extracted from ``teammates.py`` so event logic is reusable and
testable independent of the teammate loop.
"""
from __future__ import annotations

import time
from typing import Any


def now_ms() -> int:
    """Current time in milliseconds (for event timestamps)."""
    return int(time.time() * 1000)


# ── Event type constants ──
# These are the ``type`` field values inside the event payload dict
# (not the agent_core EVENT_KIND, which is the first arg to emit).
TEAM_MEMBER_SPAWNED = "team.member.spawned"
TEAM_MEMBER_DONE = "team.member.status_changed"
TEAM_MEMBER_SHUTDOWN = "team.member.shutdown"
TEAM_TASK_CREATED = "task.created"
TEAM_TASK_COMPLETED = "task.completed"
TEAM_MESSAGE = "team.message.p2p"


def emit_team_event(boss_session: Any, kind: str, event_obj: dict) -> None:
    """Best-effort emit a ``team.*`` event on the boss session's sinks.

    The boss session is the gateway chat session that started the team;
    its sinks feed the EventPipe → WS drain → frontend TeamArea.
    Thread-safe (``session.emit`` holds its own lock). Never raises —
    team visualization must not break the team engine.

    ``kind`` is an agent_core EVENT_KIND (``team_member`` / ``team_task`` / …);
    the wire layer maps it to the dotted ``team.*`` event.
    """
    if boss_session is None:
        return
    try:
        boss_session.emit(kind, {"event": event_obj})
    except Exception:
        pass


class TeammateEventShim:
    """Events shim for teammate ``chat_create``.

    Exposes ``interrupted`` (proxied from the boss session so the
    adapter's mid-stream check breaks when the user hits Stop) and
    swallows ``emit()`` so teammate tokens don't stream into the main
    chat.
    """

    __slots__ = ("_boss",)

    def __init__(self, boss: Any) -> None:
        self._boss = boss

    @property
    def interrupted(self) -> bool:
        return bool(getattr(self._boss, "interrupted", False))

    def streaming(self) -> bool:  # adapter may read this; not required
        return False

    def emit(self, *args: Any, **kwargs: Any) -> None:
        pass
