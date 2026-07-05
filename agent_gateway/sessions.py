"""Session manager: one agent core Session per chat session.

Each GatewaySession owns:
  - a code.Session (the agent core state) with an AsyncQueueSink attached;
  - an asyncio.Queue the WS/SSE pump drains (filled thread-safely by the sink);
  - a replay buffer (seq → frame) for reconnect/resume (Last-Event-ID or WS resume);
  - a pending-permissions map (request_id → Future) the FuturePermission blocks on;
  - a worker thread that runs code.agent_loop per posted user message.

The agent loop runs in a thread (it's synchronous); events bridge to the async
WS/SSE pump via loop.call_soon_threadsafe(queue.put_nowait, ...).
"""
from __future__ import annotations
import asyncio
import threading
import uuid
import queue as _queue
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Optional

import code
from code import Session, EventSink, FuturePermission

# Replay buffer caps (spec §7): 1000 events or 4MB, whichever first.
MAX_BUFFER_EVENTS = 1000
MAX_BUFFER_BYTES = 4 * 1024 * 1024
PERMISSION_TIMEOUT = 120.0


class ThreadQueueSink(EventSink):
    """EventSink that pushes frames onto a thread-safe queue.Queue.

    Also appends each frame to a replay buffer so reconnecting clients (WS
    `resume` or SSE `Last-Event-ID`) can fetch missed events. Using queue.Queue
    (not asyncio.Queue) avoids event-loop affinity issues: the agent worker
    thread puts from any thread; the async WS/SSE pump drains via
    run_in_executor.
    """
    streaming = True

    def __init__(self, out: "queue.Queue", buffer: deque, buffer_lock: threading.Lock):
        self._out = out
        self._buffer = buffer
        self._buffer_lock = buffer_lock

    def emit(self, kind: str, payload: dict):
        seq = payload.get("seq", 0)
        frame = {"seq": seq, "kind": kind, "payload": payload}
        with self._buffer_lock:
            self._buffer.append(frame)
        self._out.put(frame)  # thread-safe, unbounded


@dataclass
class GatewaySession:
    session_id: str
    transport: str                       # ws | sse (auto resolved by caller)
    agent: Session
    queue: "_queue.Queue"
    buffer: deque
    buffer_lock: threading.Lock
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
            self.last_activity = __import__("time").time()
            self.agent.history.append({"role": "user", "content": text})
            self.agent.interrupted = False
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

    # ── replay for reconnect ──

    def snapshot_since(self, last_seq: int) -> list[dict]:
        """Return buffered frames with seq > last_seq, in order."""
        with self.buffer_lock:
            return [f for f in self.buffer if f["seq"] > last_seq]


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, GatewaySession] = {}
        self._lock = threading.Lock()

    def create(self, transport: str = "auto", loop: asyncio.AbstractEventLoop = None) -> GatewaySession:
        sid = uuid.uuid4().hex[:16]
        loop = loop or asyncio.get_event_loop()
        q: "_queue.Queue" = _queue.Queue()
        buf: deque = deque(maxlen=MAX_BUFFER_EVENTS)
        buf_lock = threading.Lock()
        sink = ThreadQueueSink(q, buf, buf_lock)
        # FuturePermission resolves via the session's pending-permissions map.
        permission = FuturePermission(resolver=None, timeout=PERMISSION_TIMEOUT)
        agent = Session(transport=transport if transport in ("ws", "sse") else "ws",
                        sinks=[sink], permission=permission,
                        context=code.update_context({}, []))
        gs = GatewaySession(session_id=sid, transport=agent.transport,
                            agent=agent, queue=q, buffer=buf, buffer_lock=buf_lock,
                            loop=loop)
        permission.resolver = gs._resolver  # bind now that the session exists
        with self._lock:
            self._sessions[sid] = gs
        return gs

    def get(self, sid: str) -> Optional[GatewaySession]:
        with self._lock:
            return self._sessions.get(sid)

    def all(self) -> list[GatewaySession]:
        with self._lock:
            return list(self._sessions.values())

    def drop(self, sid: str):
        with self._lock:
            self._sessions.pop(sid, None)


manager = SessionManager()
