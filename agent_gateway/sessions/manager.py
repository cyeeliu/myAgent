"""SessionManager — owns the live session registry + hydration from the DB.

One ``code.Session`` per chat session, owned by a worker thread that runs
``agent_loop`` per posted message. Postgres (``db.py``) holds durable history;
the in-memory ``_sessions`` dict is a cache of live (transport-attached) sessions.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from typing import Optional

from agent_core import Session, FuturePermission, update_context
from agent_gateway import db
from agent_gateway import pipe as pipe_mod

from ._constants import PERMISSION_TIMEOUT, SESSION_STATE_ROOT
from .gateway_session import GatewaySession, PipeSink, ChatRecordSink
from .replay import synthesize_frames, _last_todos_from_record
from .files import _write_session_files

# session_id must be safe path characters only — no /, \, .., etc.
_SID_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def validate_session_id(sid: str) -> bool:
    """Return True if sid is a safe session identifier (no path traversal)."""
    return bool(sid) and bool(_SID_RE.match(sid))


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, GatewaySession] = {}
        self._lock = threading.Lock()

    def _build(self, sid: str, transport: str, loop: asyncio.AbstractEventLoop,
               chat_record: Optional[list] = None,
               llm_context: Optional[list] = None,
               created_at: Optional[float] = None,
               last_activity: Optional[float] = None,
               mode: Optional[str] = None) -> GatewaySession:
        """Construct a GatewaySession with its pipes/sink/agent, optionally
        seeded with persisted chat_record + llm_context (hydration)."""
        loop = loop or asyncio.get_event_loop()
        ep = pipe_mod.make_pipe(sid)             # live:{sid} token events
        chat_pipe = pipe_mod.make_chat_pipe(sid) # chat:{sid} message record
        ctx_store = pipe_mod.make_ctx_store(sid) # ctx:{sid} context snapshot
        sink = PipeSink(ep)
        permission = FuturePermission(resolver=None, timeout=PERMISSION_TIMEOUT)
        agent = Session(transport=transport if transport in ("ws", "sse") else "ws",
                        sinks=[sink], permission=permission,
                        context=update_context({}, []))
        agent.record_sinks = [ChatRecordSink(chat_pipe)]
        agent.workdir = SESSION_STATE_ROOT / sid
        # Rehydrate the mode flag so a plan-mode session resumed after idle
        # eviction / replica crash keeps its read-only restriction. The DB mode
        # is the source of truth (chat.send persists it; _run_turn updates it
        # on exit_plan_mode approval). team_mode restore needs team-name
        # resolution and only acts on turn 1 — left for later.
        if mode == "agent.plan":
            agent.context["plan_mode"] = True
        if chat_record:
            agent.record = list(chat_record)
            # Repopulate per-session todos from the last todo_write in the record
            # so has_active_todos() / the nudge stay correct after hydrate, and
            # the TodoList panel has state before any new todo_write fires.
            agent.todos = _last_todos_from_record(agent.record)
            # llm_context may be missing on old/half-migrated rows; derive from
            # the full record (uncompacted) so the first turn has a working context.
            agent.context_messages = list(llm_context) if llm_context else list(chat_record)
            # Re-seed the live pipe with synthesized replay frames so a
            # reconnecting client (WS last_seq=0) rebuilds the full conversation.
            if ep.count() == 0:
                frames = synthesize_frames(agent.record)
                if frames:
                    ep.seed(frames)
                    agent._seq = frames[-1]["seq"]
                else:
                    agent._seq = 0
            else:
                agent._seq = ep.count()
            # Re-seed the chat stream too if it expired (Redis lost the hot record).
            if chat_pipe.count() == 0:
                chat_pipe.seed(agent.record)
            # Materialize on-disk session files so the SessionsPanel file browser
            # has previewable content for a hydrated (restored) session.
            _write_session_files(sid, agent.record)
        else:
            # No chat_record → genuinely fresh session (minted id) OR a self-heal
            # of a stale sid that isn't in the DB (frontend reused a session_id
            # from sessionStorage after a gateway/DB restart). Redis is a separate
            # container and survives a gateway restart, so stream:{sid}/chat:{sid}
            # may still hold the PREVIOUS session's frames (24h TTL). If we leave
            # them, agent._seq stays 0 while the pipe has N old frames, and the
            # first chat.send's drain runs replay_since(0) → replays every old
            # frame → the previous conversation floods the "new" session. Clear any
            # stale pipe state so the fresh session starts clean.
            if ep.count() > 0:
                ep.clear()
            if chat_pipe.count() > 0:
                chat_pipe.clear()
            agent._seq = 0
        gs = GatewaySession(session_id=sid, transport=agent.transport,
                            agent=agent, pipe=ep, chat_pipe=chat_pipe,
                            ctx_store=ctx_store, loop=loop,
                            created_at=created_at or time.time(),
                            last_activity=last_activity or time.time())
        permission.resolver = gs._resolver
        agent.ask_resolver = gs.ask_resolver
        # When a background task finishes after the turn ended, re-trigger the
        # loop so the model reacts to the result instead of orphaning it until
        # the next user message.
        agent.on_background_complete = gs._on_background_complete
        # A2A: when a teammate sends a result/message to the boss, re-invoke
        # the boss session with a fresh turn instead of blocking on `wait`.
        from agent_core.bus import register_team_callback
        from agent_core.env import session_dir
        try:
            register_team_callback(str(session_dir()), gs._on_team_message)
        except Exception:
            pass
        return gs

    def create(self, transport: str = "auto", loop: asyncio.AbstractEventLoop = None,
               sid: Optional[str] = None) -> GatewaySession:
        """Create a new live session. If `sid` is given, hydrate from the DB
        (used to revive a persisted session); otherwise mint a new id and row."""
        if sid is not None and not validate_session_id(sid):
            raise ValueError(f"invalid session_id: {sid!r} (must match [A-Za-z0-9_-]+)")
        if sid is None:
            sid = uuid.uuid4().hex[:16]
        loop = loop or asyncio.get_event_loop()
        chat_record = None
        llm_context = None
        created_at = None
        last_activity = None
        row = db.load_session(sid) if sid else None
        if row is not None:
            chat_record = row.get("chat_record") or []
            llm_context = row.get("llm_context") or []
            created_at = row.get("created_at")
            last_activity = row.get("last_activity")
        gs = self._build(sid, transport, loop, chat_record, llm_context,
                         created_at, last_activity, row.get("mode") if row else None)
        if row is None:
            db.create_session_row(gs.session_id, gs.transport, gs.created_at, gs._title())
        with self._lock:
            self._sessions[sid] = gs
        return gs

    def get(self, sid: str) -> Optional[GatewaySession]:
        with self._lock:
            return self._sessions.get(sid)

    def get_or_hydrate(self, sid: str, loop: asyncio.AbstractEventLoop = None) -> Optional[GatewaySession]:
        """Return the live session for sid, hydrating from the DB if it exists
        there but isn't currently in memory. None if neither."""
        if not validate_session_id(sid):
            return None
        with self._lock:
            gs = self._sessions.get(sid)
        if gs is not None:
            return gs
        row = db.load_session(sid)
        if row is None:
            return None
        gs = self._build(sid, row.get("transport") or "ws", loop,
                         row.get("chat_record") or [], row.get("llm_context") or [],
                         row.get("created_at"), row.get("last_activity"),
                         row.get("mode"))
        with self._lock:
            self._sessions[sid] = gs
        return gs

    def all(self) -> list[GatewaySession]:
        with self._lock:
            return list(self._sessions.values())

    def drop(self, sid: str):
        with self._lock:
            gs = self._sessions.pop(sid, None)
        if gs is not None:
            # Unregister the A2A team callback so stale messages don't try to
            # re-invoke a dropped session.
            try:
                from agent_core.bus import unregister_team_callback
                from agent_core.env import session_dir
                unregister_team_callback(str(session_dir()))
            except Exception:
                pass


# Module-level singleton — the single source of truth for all gateway modules.
manager = SessionManager()
