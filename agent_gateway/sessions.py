"""Session manager: one agent core Session per chat session.

Each GatewaySession owns:
  - a code.Session (the agent core state) with a PipeSink attached;
  - an EventPipe (in-memory queue+deque OR Redis Streams) the WS/SSE pump drains;
  - a pending-permissions map (request_id → Future) the FuturePermission blocks on;
  - a worker thread that runs code.agent_loop per posted user message.

The agent loop runs in a thread (synchronous); events bridge to the async WS/SSE
pump via the EventPipe (see pipe.py). Postgres (db.py) holds durable history.
"""
from __future__ import annotations
import asyncio
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Optional

import code
from code import Session, EventSink, FuturePermission
from . import db
from . import pipe as pipe_mod

PERMISSION_TIMEOUT = 120.0

# Internal user prompts injected by the agent (max-tokens continuation, etc.)
# — skipped during replay synthesis so they don't render as user bubbles.
CONTINUATION_PROMPT = "Continue from the previous response. Do not repeat completed work."


def _stringify(content: Any) -> str:
    """Flatten a message content (str or list of blocks) to a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif "text" in b:
                    parts.append(b.get("text", ""))
        return "".join(parts)
    return str(content)


def synthesize_frames(history: list) -> list[dict]:
    """Rebuild replay frames from persisted history so a freshly hydrated
    session can replay its full conversation. Returns frames with seq 1..N;
    the caller seeds them into the pipe and advances agent._seq to N."""
    seq = 0
    frames: list[dict] = []
    for msg in history:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            if isinstance(content, str):
                if content == CONTINUATION_PROMPT or content.startswith("[Compacted."):
                    continue  # internal prompt, not a real user turn
                seq += 1
                frames.append({"seq": seq, "kind": "user", "payload": {"text": content, "seq": seq}})
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_result":
                        seq += 1
                        frames.append({"seq": seq, "kind": "tool_result",
                                       "payload": {"id": b.get("tool_use_id") or b.get("id"),
                                                   "content": _stringify(b.get("content")),
                                                   "blocked": bool(b.get("is_error")),
                                                   "seq": seq}})
        elif role == "assistant":
            if isinstance(content, str):
                seq += 1
                frames.append({"seq": seq, "kind": "token", "payload": {"text": content, "seq": seq}})
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        seq += 1
                        frames.append({"seq": seq, "kind": "token",
                                       "payload": {"text": b.get("text", ""), "seq": seq}})
                    elif b.get("type") == "tool_use":
                        seq += 1
                        frames.append({"seq": seq, "kind": "tool_start",
                                       "payload": {"id": b.get("id"), "name": b.get("name"),
                                                   "input": b.get("input", {}), "seq": seq}})
    return frames


class PipeSink(EventSink):
    """EventSink that publishes frames to the session's EventPipe."""
    streaming = True

    def __init__(self, ep: pipe_mod.EventPipe):
        self._pipe = ep

    def emit(self, kind: str, payload: dict):
        seq = payload.get("seq", 0)
        self._pipe.publish(seq, kind, payload)


@dataclass
class GatewaySession:
    session_id: str
    transport: str                       # ws | sse (auto resolved by caller)
    agent: Session
    pipe: pipe_mod.EventPipe
    loop: asyncio.AbstractEventLoop
    pending_permissions: dict[str, Future] = field(default_factory=dict)
    _perm_lock: threading.Lock = field(default_factory=threading.Lock)
    _worker: Optional[threading.Thread] = None
    _worker_lock: threading.Lock = field(default_factory=threading.Lock)
    created_at: float = field(default_factory=lambda: __import__("time").time())
    last_activity: float = field(default_factory=lambda: __import__("time").time())

    # ── permission future plumbing ──

    def _resolver(self, block, request_id: str) -> Future:
        fut: Future = Future()
        with self._perm_lock:
            self.pending_permissions[request_id] = fut
        return fut

    def grant(self, request_id: str, allow: bool, modify: Optional[str] = None) -> bool:
        with self._perm_lock:
            fut = self.pending_permissions.pop(request_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result({"allow": allow, "modify": modify})
        return True

    # ── user message → run one agent turn in a thread ──

    def post_message(self, text: str) -> bool:
        """Append a user message and run one agent_loop turn in a worker thread.
        Returns False if a turn is already in flight."""
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return False
            self.last_activity = time.time()
            self.agent.history.append({"role": "user", "content": text})
            self.agent.interrupted = False
            # Record the user message in the event pipe so a reconnecting client
            # (WS last_seq=0 replay) rebuilds the user bubble. Without this the
            # stream only has token/tool frames and user messages vanish on refresh.
            self.agent.emit("user", {"text": text})
            # Persist the user message immediately so a mid-turn crash keeps it.
            db.save_history(self.session_id, self.agent.history,
                            self.last_activity, self._title())
            t = threading.Thread(target=self._run_turn, name=f"agent-{self.session_id}",
                                  daemon=True)
            self._worker = t
            t.start()
        return True

    def _run_turn(self):
        try:
            with self.agent.lock:
                code.agent_loop(self.agent)
        except Exception as e:  # never let the worker die silently
            try:
                self.agent.emit("error", {"error": f"agent_loop crashed: {type(e).__name__}: {e}"})
                self.agent.emit("done", {"reason": "crash"})
            except Exception:
                pass
        finally:
            # Persist the full history at turn end (DB is the durable source).
            try:
                db.save_history(self.session_id, self.agent.history,
                                time.time(), self._title())
            except Exception:
                pass  # persistence failure must not mask the turn result

    def interrupt(self):
        self.agent.interrupted = True

    # ── listing metadata ──

    def _title(self) -> str:
        """Derive a short title from the first user message in history."""
        for h in self.agent.history:
            if not isinstance(h, dict) or h.get("role") != "user":
                continue
            c = h.get("content")
            if isinstance(c, str):
                return c.strip()[:60] or "(new session)"
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text":
                        t = (b.get("text") or "").strip()
                        if t:
                            return t[:60]
        return "(new session)"

    def meta(self) -> dict:
        return {
            "session_id": self.session_id,
            "transport": self.transport,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "title": self._title(),
            "history_len": len(self.agent.history),
        }


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, GatewaySession] = {}
        self._lock = threading.Lock()

    def _build(self, sid: str, transport: str, loop: asyncio.AbstractEventLoop,
               history: Optional[list] = None,
               created_at: Optional[float] = None,
               last_activity: Optional[float] = None) -> GatewaySession:
        """Construct a GatewaySession with its pipe/sink/agent, optionally
        seeded with persisted history (hydration)."""
        loop = loop or asyncio.get_event_loop()
        ep = pipe_mod.make_pipe(sid)
        sink = PipeSink(ep)
        permission = FuturePermission(resolver=None, timeout=PERMISSION_TIMEOUT)
        agent = Session(transport=transport if transport in ("ws", "sse") else "ws",
                        sinks=[sink], permission=permission,
                        context=code.update_context({}, []))
        if history:
            agent.history = list(history)
            # Seed the pipe with synthesized replay frames so a reconnecting
            # client (WS last_seq=0) rebuilds the full conversation. If the
            # pipe already has events (Redis stream still hot), skip seeding.
            if ep.count() == 0:
                frames = synthesize_frames(agent.history)
                if frames:
                    ep.seed(frames)
                    agent._seq = frames[-1]["seq"]
                else:
                    agent._seq = 0
            else:
                agent._seq = ep.count()
        gs = GatewaySession(session_id=sid, transport=agent.transport,
                            agent=agent, pipe=ep, loop=loop,
                            created_at=created_at or time.time(),
                            last_activity=last_activity or time.time())
        permission.resolver = gs._resolver
        return gs

    def create(self, transport: str = "auto", loop: asyncio.AbstractEventLoop = None,
               sid: Optional[str] = None) -> GatewaySession:
        """Create a new live session. If `sid` is given, hydrate from the DB
        (used to revive a persisted session); otherwise mint a new id and row."""
        if sid is None:
            sid = uuid.uuid4().hex[:16]
        loop = loop or asyncio.get_event_loop()
        history = None
        created_at = None
        last_activity = None
        row = db.load_session(sid) if sid else None
        if row is not None:
            history = row.get("history") or []
            created_at = row.get("created_at")
            last_activity = row.get("last_activity")
        gs = self._build(sid, transport, loop, history, created_at, last_activity)
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
        with self._lock:
            gs = self._sessions.get(sid)
        if gs is not None:
            return gs
        row = db.load_session(sid)
        if row is None:
            return None
        gs = self._build(sid, row.get("transport") or "ws", loop,
                         row.get("history") or [], row.get("created_at"),
                         row.get("last_activity"))
        with self._lock:
            self._sessions[sid] = gs
        return gs

    def all(self) -> list[GatewaySession]:
        with self._lock:
            return list(self._sessions.values())

    def drop(self, sid: str):
        with self._lock:
            self._sessions.pop(sid, None)


manager = SessionManager()
