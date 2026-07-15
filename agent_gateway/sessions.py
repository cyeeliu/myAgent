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
import json
import logging
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import code

from .debug import debug

_log = logging.getLogger(__name__)
from code import Session, EventSink, FuturePermission
from . import db
from . import pipe as pipe_mod

PERMISSION_TIMEOUT = 120.0

# Internal user prompts injected by the agent (max-tokens continuation, the
# todo nudge, explicit-compaction markers) — skipped during replay synthesis
# and session-file writing so they don't render as user bubbles in the UI.
CONTINUATION_PROMPT = "Continue from the previous response. Do not repeat completed work."
TODO_REMINDER_PREFIX = "<reminder>"
TASK_NOTIFICATION_PREFIX = "<task_notification>"


def _is_internal_user_prompt(text: str) -> bool:
    """True if this user message is an agent-internal nudge, not a real turn."""
    return (text == CONTINUATION_PROMPT
            or text.startswith("[Compacted.")
            or text.startswith(TODO_REMINDER_PREFIX)
            or text.startswith(TASK_NOTIFICATION_PREFIX))

# On-disk session artifacts root. The jiuwenswarm frontend's SessionsPanel browses
# `agent/sessions/{sid}/` via the /file-api REST routes (rooted at REPO_ROOT);
# writing the conversation here makes the file list non-empty and previewable.
SESSION_FILES_ROOT = code.REPO_ROOT / "agent" / "sessions"

# Session-bound state (.tasks/.transcripts/.task_outputs/.worktrees/.mailboxes/
# .scheduled_tasks.json) lives under SESSION_STATE_ROOT/<sid>/ — a hidden
# .sessions/ branch inside the mounted workspace. Nesting under the mount keeps
# it persisted across container restarts (durable cron, worktrees), and the
# leading-dot name keeps it out of the AgentPanel file browser (which skips
# dot-dirs other than .memory). The shared workspace root is set once at import.
code.set_workspace_dir(code.REPO_ROOT / "workspace")
SESSION_STATE_ROOT = code.workspace_dir() / ".sessions"


def cleanup_session_artifacts(sid: str) -> None:
    """Remove all on-disk + Redis artifacts for a session. Called on delete so
    the SessionsPanel file browser and the workspace .sessions/ tree don't
    accumulate stale dirs, and a reused session_id doesn't hydrate from a
    previous run's leftover state. Best-effort: a cleanup failure must not
    block the delete (the Postgres row is already gone by the time this runs
    in the delete path). Idempotent — missing dirs/keys are fine."""
    if not sid or not isinstance(sid, str) or "/" in sid or "\\" in sid or sid in (".", ".."):
        return  # guard against path traversal / bogus ids
    import shutil
    # 1. On-disk session files (transcript.md, history.json) under /app/agent/sessions/<sid>/
    try:
        shutil.rmtree(SESSION_FILES_ROOT / sid, ignore_errors=True)
    except Exception:
        pass
    # 2. Session-bound state (.tasks/.transcripts/.task_outputs/.worktrees/
    #    .mailboxes/.scheduled_tasks.json) under workspace/.sessions/<sid>/
    try:
        shutil.rmtree(SESSION_STATE_ROOT / sid, ignore_errors=True)
    except Exception:
        pass
    # 3. Redis hot pipes (live stream / chat record stream / ctx hash). These
    #    have a 24h TTL so they'd expire anyway, but deleting now frees memory
    #    immediately and prevents a same-id resurrect from seeing stale frames.
    try:
        if pipe_mod.redis_enabled():
            r = pipe_mod._sync_r
            r.delete(f"stream:{sid}", f"chat:{sid}", f"ctx:{sid}")
    except Exception:
        pass


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
            if code._block_type(b) == "text":
                parts.append(code._block_attr(b, "text", "") or "")
        return "".join(parts)
    return str(content)


def _write_session_files(sid: str, record: list) -> None:
    """Persist the conversation to `agent/sessions/{sid}/` as a readable
    transcript.md and a raw history.json so the SessionsPanel file browser
    (which browses that dir via /file-api) has previewable content. Best-effort:
    failures are swallowed (the DB is the source of truth, not these files).

    history.json is written in the shape the jiuwenswarm SessionsPanel preview
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


def _last_todos_from_record(record: list) -> list:
    """Scan the chat record for the last todo_write tool_use and return its
    raw `todos` input (or [] if none). Used to repopulate Session.todos on
    hydrate so has_active_todos() stays correct across eviction/restart, and
    the nudge logic doesn't lose track of unfinished todos."""
    last: list | None = None
    for msg in record or []:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if code._block_type(b) == "tool_use" and code._block_attr(b, "name") == "todo_write":
                inp = code._block_attr(b, "input", {})
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
                    if code._block_type(b) == "tool_result":
                        seq += 1
                        frames.append({"seq": seq, "kind": "tool_result",
                                       "payload": {"id": code._block_attr(b, "tool_use_id") or code._block_attr(b, "id"),
                                                   "content": _stringify(code._block_attr(b, "content")),
                                                   "blocked": bool(code._block_attr(b, "is_error")),
                                                   "seq": seq}})
        elif role == "assistant":
            if isinstance(content, str):
                seq += 1
                frames.append({"seq": seq, "kind": "token", "payload": {"text": content, "seq": seq}})
            elif isinstance(content, list):
                for b in content:
                    bt = code._block_type(b)
                    if bt == "text":
                        seq += 1
                        frames.append({"seq": seq, "kind": "token",
                                       "payload": {"text": code._block_attr(b, "text", ""), "seq": seq}})
                    elif bt == "tool_use":
                        seq += 1
                        frames.append({"seq": seq, "kind": "tool_start",
                                       "payload": {"id": code._block_attr(b, "id"),
                                                   "name": code._block_attr(b, "name"),
                                                   "input": code._block_attr(b, "input", {}), "seq": seq}})
                        # Track the last todo_write so a reconnect whose live
                        # stream expired (>24h, replay synthesized from the
                        # chat record) still repopulates the TodoList panel.
                        if code._block_attr(b, "name") == "todo_write":
                            inp = code._block_attr(b, "input", {})
                            tlist = inp.get("todos") if isinstance(inp, dict) else None
                            if isinstance(tlist, list):
                                last_todos = tlist
                        # Re-emit show_widget artifacts positionally so a
                        # synthesized (>24h) reconnect still renders the SVG/HTML
                        # widget the agent produced. Within 24h the original live
                        # `widget` frame is replayed from the EventPipe.
                        if code._block_attr(b, "name") == "show_widget":
                            inp = code._block_attr(b, "input", {})
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
                       "payload": {"todos": code.todo_payload(last_todos), "seq": seq}})
    return frames


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
        Returns False if a turn is already in flight."""
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
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
        try:
            if self.agent.workdir is not None:
                code.set_workdir(self.agent.workdir)
            with self.agent.lock:
                code.agent_loop(self.agent)
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
            # Refresh on-disk session files so the browser reflects the new turn.
            _write_session_files(self.session_id, self.agent.record)
            # Clear the worker so the next post_message isn't rejected as
            # "in flight" after the turn has ended.
            with self._worker_lock:
                self._worker = None
            debug("turn end sid=%r seq=%r record_len=%d",
                  self.session_id, getattr(self.agent, "_seq", 0), len(self.agent.record or []))
            # Race backstop: a background task may have completed between the
            # last inject_background_notifications pass and this point. Re-check
            # and, if anything is pending, start a follow-up turn to deliver it.
            try:
                self._on_background_complete()
            except Exception:
                pass

    def interrupt(self):
        self.agent.interrupted = True

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
            notifications = code.collect_background_results()
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
                    if code._block_type(b) == "text":
                        t = (code._block_attr(b, "text", "") or "").strip()
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


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, GatewaySession] = {}
        self._lock = threading.Lock()

    def _build(self, sid: str, transport: str, loop: asyncio.AbstractEventLoop,
               chat_record: Optional[list] = None,
               llm_context: Optional[list] = None,
               created_at: Optional[float] = None,
               last_activity: Optional[float] = None) -> GatewaySession:
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
                        context=code.update_context({}, []))
        agent.record_sinks = [ChatRecordSink(chat_pipe)]
        agent.workdir = SESSION_STATE_ROOT / sid
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
        return gs

    def create(self, transport: str = "auto", loop: asyncio.AbstractEventLoop = None,
               sid: Optional[str] = None) -> GatewaySession:
        """Create a new live session. If `sid` is given, hydrate from the DB
        (used to revive a persisted session); otherwise mint a new id and row."""
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
                         created_at, last_activity)
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
                         row.get("chat_record") or [], row.get("llm_context") or [],
                         row.get("created_at"), row.get("last_activity"))
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
