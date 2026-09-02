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
    adapter's mid-stream check breaks when the user hits Stop).

    T-M5: ``emit()`` forwards meaningful events (tool_start, tool_result,
    done, error) to the boss session as ``team_event`` events so the
    frontend can see teammate activity in real time.  Token / text /
    tool_start_delta events are still swallowed to avoid flooding the
    main chat with per-token deltas.
    """

    # Event kinds that are forwarded to the boss session.
    _FORWARD_KINDS = frozenset({
        "tool_start", "tool_result", "done", "error",
    })

    __slots__ = ("_boss", "_name")

    def __init__(self, boss: Any, name: str = "") -> None:
        self._boss = boss
        self._name = name

    @property
    def interrupted(self) -> bool:
        return bool(getattr(self._boss, "interrupted", False))

    def streaming(self) -> bool:  # adapter may read this; not required
        return False

    def emit(self, kind: str = "", *args: Any, **kwargs: Any) -> None:
        # Forward meaningful events to the boss session as team_event
        # so the frontend TeamArea shows teammate tool calls / results /
        # completion / errors in real time.
        if kind not in self._FORWARD_KINDS or self._boss is None:
            return
        try:
            # T-M5 bug-2 fix: adapter calls emit(kind, payload_dict)
            # positionally, so payload is in args[0], not kwargs.
            if args and isinstance(args[0], dict):
                payload = args[0]
            elif args and isinstance(args[0], str):
                payload = {"text": args[0]}
            else:
                payload = kwargs.get("payload") or kwargs or {}
            if isinstance(payload, dict):
                payload = {**payload, "teammate": self._name}
            self._boss.emit("team_event", {
                "event": {
                    "type": f"teammate.{kind}",
                    "teammate": self._name,
                    "payload": payload,
                },
            })
        except Exception:
            pass
