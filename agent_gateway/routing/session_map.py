"""SessionMap — channel↔session binding (mirrors jiuwenswarm routing/session_map).

Maps a (channel_id, channel_local_key) → session_id so an inbound IM message
lands on the same session across turns. For the web channel the session_id is
carried in every req param, so the map is mostly a passthrough; for IM channels
it's the source of truth for "which session does this DM belong to".
"""
from __future__ import annotations
import threading
from typing import Optional


class SessionMap:
    def __init__(self):
        self._lock = threading.Lock()
        self._map: dict[tuple[str, str], str] = {}

    def bind(self, channel_id: str, local_key: str, session_id: str) -> None:
        with self._lock:
            self._map[(channel_id, local_key)] = session_id

    def lookup(self, channel_id: str, local_key: str) -> Optional[str]:
        with self._lock:
            return self._map.get((channel_id, local_key))

    def unbind(self, channel_id: str, local_key: str) -> None:
        with self._lock:
            self._map.pop((channel_id, local_key), None)

    def sessions_for_channel(self, channel_id: str) -> list[str]:
        with self._lock:
            return [sid for (cid, _), sid in self._map.items() if cid == channel_id]
