"""Thread-safe registry for team runtime state.

Encapsulates the three module-level dicts that ``teammates.py``
previously kept as bare globals (``active_teammates``,
``_team_leaders``, ``_team_boss_sessions``). All access goes through
methods that hold a lock, so concurrent teammate threads and the main
loop can safely read/write team state.

Backward compatibility: ``teammates.py`` exposes ``active_teammates``
and ``_team_leaders`` as proxy objects that delegate to this registry,
so existing ``from agent_core.teammates import active_teammates`` and
``context.py``'s ``list(active_teammates.keys())`` keep working.
"""
from __future__ import annotations

import threading
from typing import Any


class TeamRegistry:
    """Thread-safe registry of active teammates, team leaders, and boss
    sessions.

    * ``active_teammates``: name → ``str(session_dir())`` of the session
      that spawned this teammate. The value is the session dir (not a
      bool) so ``start_team`` can detect teammates left over from a
      DIFFERENT gateway session and evict them. ``bool()`` of a non-empty
      str is True, so ``team_info``'s ``bool(active_teammates.get(name))``
      keeps working.
    * ``team_leaders``: team_name → leader teammate name.
    * ``boss_sessions``: team_name → boss session object.
    """

    __slots__ = ("_lock", "_active", "_leaders", "_bosses", "_heartbeats")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, str] = {}
        self._leaders: dict[str, str] = {}
        self._bosses: dict[str, Any] = {}
        self._heartbeats: dict[str, float] = {}  # name → last-active monotonic time

    # ── active teammates ──

    def register_teammate(self, name: str, session_dir_str: str) -> bool:
        """Register a teammate. Returns ``True`` if registered, ``False``
        if a teammate with the same name is already active in the same
        session (genuine duplicate). Stale entries from a different
        session are evicted automatically."""
        import time
        with self._lock:
            existing = self._active.get(name)
            if existing == session_dir_str:
                return False  # genuine duplicate
            if existing is not None:
                # Stale entry from a different session — evict it.
                self._active.pop(name, None)
            self._active[name] = session_dir_str
            self._heartbeats[name] = time.monotonic()
            return True

    def unregister_teammate(self, name: str, session_dir_str: str) -> None:
        """Remove a teammate entry, but only if it still belongs to the
        given session. A newer session may have evicted and replaced
        this name — don't clobber theirs."""
        with self._lock:
            if self._active.get(name) == session_dir_str:
                self._active.pop(name, None)
                self._heartbeats.pop(name, None)

    def heartbeat(self, name: str) -> None:
        """Update the last-active timestamp for a teammate. Called each
        iteration of the teammate loop so the watchdog can distinguish
        'alive and working' from 'registered but stuck/crashed'."""
        import time
        with self._lock:
            if name in self._active:
                self._heartbeats[name] = time.monotonic()

    def get_heartbeats(self) -> dict[str, float]:
        """Snapshot of all heartbeat timestamps (monotonic)."""
        with self._lock:
            return dict(self._heartbeats)

    def is_active(self, name: str) -> bool:
        """Whether a teammate by ``name`` is currently registered."""
        with self._lock:
            return bool(self._active.get(name))

    def get_session_dir(self, name: str) -> str | None:
        """The ``str(session_dir())`` of the session that spawned this
        teammate, or ``None`` if not active."""
        with self._lock:
            return self._active.get(name)

    def active_names(self) -> list[str]:
        """Snapshot of all active teammate names."""
        with self._lock:
            return list(self._active.keys())

    def active_names_for_session(self, session_dir_str: str) -> list[str]:
        """Snapshot of active teammate names belonging to a specific session.

        Fixes C-H9: active_names() returns teammates across ALL sessions,
        causing cross-session leakage in update_context. This method
        filters by the session_dir_str that was passed to register_teammate.
        """
        with self._lock:
            return [name for name, sd in self._active.items()
                    if sd == session_dir_str]

    def active_dict(self) -> dict[str, str]:
        """Snapshot copy of the full active-teammate dict."""
        with self._lock:
            return dict(self._active)

    # ── team leaders ──

    def set_leader(self, team_name: str, leader_name: str) -> None:
        with self._lock:
            self._leaders[team_name] = leader_name

    def get_leader(self, team_name: str) -> str | None:
        with self._lock:
            return self._leaders.get(team_name)

    # ── boss sessions ──

    def set_boss_session(self, team_name: str, session: Any) -> None:
        with self._lock:
            self._bosses[team_name] = session

    def get_boss_session(self, team_name: str) -> Any:
        with self._lock:
            return self._bosses.get(team_name)

    def remove_boss_session(self, team_name: str) -> None:
        with self._lock:
            self._bosses.pop(team_name, None)

    # ── bulk cleanup ──

    def clear_team(self, team_name: str) -> None:
        """Remove all state for a team (leader + boss session).
        Active teammate entries are left — they self-clean on thread
        exit via ``unregister_teammate``."""
        with self._lock:
            self._leaders.pop(team_name, None)
            self._bosses.pop(team_name, None)


# Singleton instance — the one registry used throughout agent_core.
registry = TeamRegistry()
