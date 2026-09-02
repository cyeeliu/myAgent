"""GatewaySession — one live chat session owned by a worker thread.

Wraps a ``code.Session`` (agent core state) with:
  - an EventPipe (live token events for WS/SSE)
  - a ChatStreamPipe (append-only message-level record)
  - a ContextStore (compacted LLM context snapshot)
  - pending-permissions / pending-asks maps (Future-based)
  - a worker thread that runs ``agent_loop`` per posted user message
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Optional

import asyncio

from agent_gateway.debug import debug
from agent_core import Session, EventSink, FuturePermission, set_workdir, agent_loop, collect_background_results, _block_type, _block_attr
from agent_gateway import db
from agent_gateway import pipe as pipe_mod

from ._constants import PERMISSION_TIMEOUT
from .files import _write_session_files


class PipeSink(EventSink):
    """EventSink that publishes frames to the session's EventPipe."""
    streaming = True

    def __init__(self, ep: pipe_mod.EventPipe):
        self._pipe = ep

    def emit(self, kind: str, payload: dict):
        seq = payload.get("seq", 0)
        if kind in ("token", "done", "user", "tool_start", "tool_result", "error",
                    "history_message", "context_usage", "todo", "widget", "ask_user"):
            debug("pipe<<emit kind=%r seq=%r", kind, seq)
        self._pipe.publish(seq, kind, payload)


class ChatRecordSink:
    """Receives every append_both() call and writes it to chat:{sid} so the
    durable, never-compacted chat record stream stays live for every turn —
    not just user messages."""

    def __init__(self, chat_pipe: pipe_mod.ChatStreamPipe):
        self._pipe = chat_pipe

    def append(self, msg: dict):
        self._pipe.append(msg)


@dataclass
class GatewaySession:
    session_id: str
    transport: str                       # ws | sse (auto resolved by caller)
    agent: Session
    pipe: pipe_mod.EventPipe             # live:{sid} — token-level events for WS/SSE
    chat_pipe: pipe_mod.ChatStreamPipe   # chat:{sid} — append-only message-level record
    ctx_store: pipe_mod.ContextStore     # ctx:{sid}  — compacted LLM context snapshot
    loop: asyncio.AbstractEventLoop
    pending_permissions: dict[str, Future] = field(default_factory=dict)
    pending_asks: dict[str, Future] = field(default_factory=dict)
    _perm_lock: threading.Lock = field(default_factory=threading.Lock)
    _worker: Optional[threading.Thread] = None
    _worker_lock: threading.Lock = field(default_factory=threading.Lock)
    # User messages posted while the agent was blocked in the `wait` tool. The
    # current turn is interrupted (wait_lock.wake("user")) and these are drained
    # one per turn-end in _run_turn's finally, each starting a fresh turn.
    _pending_user_msgs: list = field(default_factory=list)
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

    # ── ask_user future plumbing ──
    # The ask_user tool emits an `ask_user` event (→ chat.ask_user_question in
    # the wire layer) and blocks on a future; the client answers via
    # chat.send{request_id, answers, source:"ask_user_interrupt"} and respond_ask
    # resolves it. Mirrors the permission future pattern.

    def ask_resolver(self, request_id: str) -> Future:
        fut: Future = Future()
        with self._perm_lock:
            self.pending_asks[request_id] = fut
        return fut

    def respond_ask(self, request_id: str, answers) -> bool:
        with self._perm_lock:
            fut = self.pending_asks.pop(request_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result(answers)
        return True

    # ── user message → run one agent turn in a thread ──

    def post_message(self, text: str) -> bool:
        """Append a user message and run one agent_loop turn in a worker thread.
        Returns False if a turn is already in flight — unless the in-flight turn
        is blocked in the `wait` tool, in which case the wait is interrupted
        (wake "user") and this message is queued to run in a fresh turn after
        the current one unwinds."""
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                # Turn in flight. If it's blocked in the wait tool, poke it so
                # the loop exits (interrupted) at the top of its next iteration
                # and this message runs in a fresh turn after it unwinds.
                # Otherwise the turn is genuinely busy (LLM call, tool exec) —
                # reject so the caller can retry / surface "busy".
                wl = getattr(self.agent, "wait_lock", None)
                if wl is not None and wl.is_waiting():
                    self.agent.interrupted = True
                    wl.wake("user", text[:200])
                    self._pending_user_msgs.append(text)
                    self.last_activity = time.time()
                    debug("post_message queued (wait-wake) sid=%r text_len=%d",
                          self.session_id, len(text))
                    return True
                debug("post_message rejected (in flight) sid=%r", self.session_id)
                return False
            self.last_activity = time.time()
            user_msg = {"role": "user", "content": text}
            # Append to the chat record (never compacted) AND the LLM context.
            # append_both also fans out to record_sinks → chat:{sid} stream.
            self.agent.append_both(user_msg)
            self.agent.interrupted = False
            # Record the user message in the live event pipe so a reconnecting
            # client (WS last_seq=0 replay) rebuilds the user bubble.
            self.agent.emit("user", {"text": text})
            # Persist the chat record immediately so a mid-turn crash keeps it.
            db.save_chat_record(self.session_id, self.agent.record,
                                self.last_activity, self._title())
            t = threading.Thread(target=self._run_turn, name=f"agent-{self.session_id}",
                                  daemon=True)
            self._worker = t
            t.start()
            debug("post_message turn started sid=%r text_len=%d", self.session_id, len(text))
        return True

    def _run_turn(self):
        debug("turn begin sid=%r seq=%r", self.session_id, getattr(self.agent, "_seq", 0))
        # Capture plan_mode at turn start so the finally block can detect an
        # exit_plan_mode approval (plan_mode popped mid-turn) and persist the
        # mode change to DB. Without this, the DB keeps "agent.plan" after
        # approval and a reconnect (via _build mode rehydrate) wrongly re-enters
        # plan mode. Surgical: only writes on the plan→non-plan transition.
        plan_at_start = bool(self.agent.context.get("plan_mode"))
        try:
            if self.agent.workdir is not None:
                set_workdir(self.agent.workdir)
            # NOTE: do NOT hold self.agent.lock around agent_loop. post_message
            # already serializes turns via _worker_lock + _worker (one worker per
            # session), and teammate threads spawned by start_team call
            # boss_session.emit() — which acquires session.lock — to surface
            # team.member/team.message events. Holding the lock for the whole
            # loop deadlocks them: the boss's `wait` blocks for teammate replies
            # while the teammates block on the lock the boss's turn holds, so the
            # wait times out and the boss reports "leader not responding".
            agent_loop(self.agent)
        except Exception as e:  # never let the worker die silently
            debug("turn CRASH sid=%r err=%s: %s", self.session_id, type(e).__name__, e)
            try:
                self.agent.emit("error", {"error": f"agent_loop crashed: {type(e).__name__}: {e}"})
                self.agent.emit("done", {"reason": "crash"})
            except Exception:
                pass
        finally:
            # Persist both stores at turn end: the append-only chat record and
            # the compacted LLM context snapshot. Redis ctx:{sid} mirrors the
            # context for fast hydrate without recompacting.
            now = time.time()
            try:
                db.save_chat_record(self.session_id, self.agent.record, now, self._title())
                db.save_llm_context(self.session_id, self.agent.context_messages, now)
                self.ctx_store.snapshot(self.agent.context_messages)
            except Exception:
                pass  # persistence failure must not mask the turn result
            # exit_plan_mode approval transitioned plan_mode True→False mid-turn;
            # persist the new mode so reconnect (_build mode rehydrate) resumes in
            # fast/executing state instead of wrongly re-entering plan mode.
            if plan_at_start and not self.agent.context.get("plan_mode"):
                try:
                    db.save_session_mode(self.session_id, "agent.fast")
                except Exception:
                    pass
            # Refresh on-disk session files so the browser reflects the new turn.
            _write_session_files(self.session_id, self.agent.record)
            # Clear the worker so the next post_message isn't rejected as
            # "in flight" after the turn has ended. Drain a user message that was
            # queued while the agent was blocked in the `wait` tool — the wait
            # was interrupted (wake "user") and this is the follow-up turn the
            # user intended. Pop under the lock; post_message starts a fresh
            # worker (and re-enters _worker_lock, which is fine — we release it
            # first). If we start a turn here, _on_background_complete below
            # sees it alive and returns, so the two never double-start.
            with self._worker_lock:
                self._worker = None
                pending = (self._pending_user_msgs.pop(0)
                           if self._pending_user_msgs else None)
            debug("turn end sid=%r seq=%r record_len=%d",
                  self.session_id, getattr(self.agent, "_seq", 0), len(self.agent.record or []))
            if pending:
                try:
                    self.post_message(pending)
                except Exception:
                    pass
                return  # the fresh turn's finally will drain the rest + bg
            # Race backstop: a background task may have completed between the
            # last inject_background_notifications pass and this point. Re-check
            # and, if anything is pending, start a follow-up turn to deliver it.
            try:
                self._on_background_complete()
            except Exception:
                pass

    def interrupt(self):
        self.agent.interrupted = True
        # If the turn is blocked in the `wait` tool, poke its WaitLock so the
        # blocked wl.wait() returns immediately instead of running to its
        # timeout. Without this, setting `interrupted` alone can't break a
        # Condition.wait, and a boss stuck in `wait` (common in cluster mode)
        # won't notice the interrupt until the wait times out (up to 3600s).
        wl = getattr(self.agent, "wait_lock", None)
        waiting = wl is not None and wl.is_waiting()
        if waiting:
            wl.wake("user", "interrupt")
        debug("interrupt sid=%r waiting=%s worker_alive=%s",
              self.session_id, waiting,
              self._worker.is_alive() if self._worker else False)

    def _on_background_complete(self):
        """Called by a background task's worker thread when it finishes.

        If a turn is still in flight, the running loop's
        inject_background_notifications will pick the result up on its next
        iteration — do nothing. If the turn has ended, pop pending results,
        inject them as a user-side notification, and start a fresh turn so the
        model can react (summarize a build, follow up, …). Without this, a
        result that lands after agent_loop returns is orphaned until the next
        user message.
        """
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return  # the running loop will drain background_results itself
            notifications = collect_background_results()
            if not notifications:
                return
            self.last_activity = time.time()
            # Context-only: inject the completed background result into the
            # LLM context so the fresh turn can react to it, but keep it out of
            # the durable chat record — <task_notification> is agent-internal
            # and must not appear in history.json / replay / as a user bubble.
            # The assistant reply this triggers is a real turn (append_both).
            self.agent.append_context({"role": "user", "content": [
                {"type": "text", "text": note} for note in notifications]})
            self.agent.interrupted = False
            db.save_chat_record(self.session_id, self.agent.record,
                                self.last_activity, self._title())
            t = threading.Thread(target=self._run_turn, name=f"agent-bg-{self.session_id}",
                                  daemon=True)
            self._worker = t
            t.start()

    # ── listing metadata ──

    def _title(self) -> str:
        """Derive a short title from the first user message in the chat record."""
        for h in self.agent.record:
            if not isinstance(h, dict) or h.get("role") != "user":
                continue
            c = h.get("content")
            if isinstance(c, str):
                return c.strip()[:60] or "(new session)"
            if isinstance(c, list):
                for b in c:
                    if _block_type(b) == "text":
                        t = (_block_attr(b, "text", "") or "").strip()
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
            "history_len": len(self.agent.record),
        }
