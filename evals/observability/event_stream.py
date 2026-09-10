"""evals.observability.event_stream — run event stream subsystem.

7 structured event types, EvalEventEmitter, EvalEventPipe, SSE subscription.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

from evals.observability import config
from evals.observability.router import ObservabilityContext


# ── 3.1 RunEvent ──

class EventType(str, Enum):
    RUN_STARTED = "run_started"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    PROGRESS = "progress"
    RUN_COMPLETED = "run_completed"
    RUN_CANCELLED = "run_cancelled"


_MAX_EVENT_BYTES = 4096


@dataclass
class RunEvent:
    event_type: str
    timestamp: float = field(default_factory=time.time)
    run_id: str = ""
    seq: int = 0
    task_id: str = ""
    rep: int = 0
    status: str = ""
    duration_ms: float = 0
    error: str = ""
    error_kind: str = ""
    judge_score: float = 0
    completed: int = 0
    total: int = 0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        raw = json.dumps(d, default=str)
        if len(raw.encode()) > _MAX_EVENT_BYTES:
            d["error"] = (d["error"] or "")[:200]
            d["extra"] = {}
            d["error_kind"] = "truncated"
        return d


# ── 3.2 EvalEventEmitter ──

class EvalEventEmitter:
    """Thread-safe event emitter with global seq generation."""

    def __init__(self):
        self._seq = 0
        self._lock = threading.Lock()
        self._pipes: dict[str, "EvalEventPipe"] = {}
        self._pipes_lock = threading.Lock()

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _get_pipe(self, run_id: str) -> "EvalEventPipe":
        with self._pipes_lock:
            if run_id not in self._pipes:
                self._pipes[run_id] = EvalEventPipe(run_id)
            return self._pipes[run_id]

    def _emit(self, event: RunEvent):
        event.seq = self._next_seq()
        pipe = self._get_pipe(event.run_id)
        try:
            pipe.publish(event.seq, event.event_type, event.to_dict())
        except Exception:
            pass

    def emit_run_started(self, run_id: str, total: int = 0, **kw):
        self._emit(RunEvent(event_type=EventType.RUN_STARTED, run_id=run_id,
                            total=total, extra=kw))

    def emit_task_started(self, run_id: str, task_id: str, rep: int = 0, **kw):
        self._emit(RunEvent(event_type=EventType.TASK_STARTED, run_id=run_id,
                            task_id=task_id, rep=rep, extra=kw))

    def emit_task_completed(self, run_id: str, task_id: str, rep: int = 0,
                            duration_ms: float = 0, judge_score: float = 0, **kw):
        self._emit(RunEvent(event_type=EventType.TASK_COMPLETED, run_id=run_id,
                            task_id=task_id, rep=rep, status="ok",
                            duration_ms=duration_ms, judge_score=judge_score, extra=kw))

    def emit_task_failed(self, run_id: str, task_id: str, rep: int = 0,
                         error: str = "", error_kind: str = "", **kw):
        self._emit(RunEvent(event_type=EventType.TASK_FAILED, run_id=run_id,
                            task_id=task_id, rep=rep, status="error",
                            error=error, error_kind=error_kind, extra=kw))

    def emit_progress(self, run_id: str, task_id: str = "", completed: int = 0,
                      total: int = 0, **kw):
        self._emit(RunEvent(event_type=EventType.PROGRESS, run_id=run_id,
                            task_id=task_id, completed=completed, total=total, extra=kw))

    def emit_run_completed(self, run_id: str, completed: int = 0, total: int = 0, **kw):
        self._emit(RunEvent(event_type=EventType.RUN_COMPLETED, run_id=run_id,
                            completed=completed, total=total, extra=kw))

    def emit_run_cancelled(self, run_id: str, **kw):
        self._emit(RunEvent(event_type=EventType.RUN_CANCELLED, run_id=run_id, extra=kw))

    def get_pipe(self, run_id: str) -> "EvalEventPipe":
        return self._get_pipe(run_id)


_emitter: Optional[EvalEventEmitter] = None
_emitter_lock = threading.Lock()


def get_emitter() -> EvalEventEmitter:
    global _emitter
    with _emitter_lock:
        if _emitter is None:
            _emitter = EvalEventEmitter()
        return _emitter


# ── 3.3 EvalEventPipe ──

class EvalEventPipe:
    """Eval-specific event pipe. Stream key: stream:eval:{run_id}."""

    def __init__(self, run_id: str):
        self._run_id = run_id
        self._impl = _make_eval_pipe_impl(run_id)

    def publish(self, seq: int, kind: str, payload: dict) -> None:
        self._impl.publish(seq, kind, payload)

    def replay_since(self, last_seq: int) -> list[dict]:
        return self._impl.replay_since(last_seq)

    async def live(self, after_seq: int):
        async for frame in self._impl.live(after_seq):
            yield frame

    def count(self) -> int:
        return self._impl.count()


def _make_eval_pipe_impl(run_id: str):
    try:
        from agent_gateway.pipe import redis_enabled, make_pipe
        return make_pipe(f"eval:{run_id}")
    except Exception:
        from collections import deque
        import queue as _q

        class _InMem:
            def __init__(self):
                self._q: _q.Queue = _q.Queue()
                self._buf: deque = deque(maxlen=1000)
                self._lock = threading.Lock()

            def publish(self, seq: int, kind: str, payload: dict) -> None:
                frame = {"seq": seq, "kind": kind, "payload": payload}
                with self._lock:
                    self._buf.append(frame)
                self._q.put(frame)

            def replay_since(self, last_seq: int) -> list[dict]:
                with self._lock:
                    return [f for f in self._buf if f["seq"] > last_seq]

            async def live(self, after_seq: int):
                last = after_seq
                while True:
                    try:
                        frame = self._q.get_nowait()
                    except _q.Empty:
                        yield None
                        await asyncio.sleep(0.05)
                        continue
                    if frame["seq"] <= last:
                        continue
                    last = frame["seq"]
                    yield frame

            def count(self) -> int:
                with self._lock:
                    return len(self._buf)

        return _InMem()


# ── 3.4 SSE ──

def _format_sse(frame: dict) -> str:
    seq = frame.get("seq", 0)
    kind = frame.get("kind", "message")
    payload = frame.get("payload", {})
    return f"id: {seq}\nevent: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"


async def _event_stream(emitter: EvalEventEmitter, run_id: str, last_seq: int):
    pipe = emitter.get_pipe(run_id)
    last = last_seq
    for frame in pipe.replay_since(last_seq):
        yield _format_sse({**frame, "payload": {**frame.get("payload", {}), "replay": True}})
        last = max(last, frame.get("seq", 0))
    last_beat = time.monotonic()
    async for tick in pipe.live(last):
        if tick is None:
            if time.monotonic() - last_beat >= config.EVAL_EVENT_SSE_HEARTBEAT:
                yield ": ping\n\n"
                last_beat = time.monotonic()
            continue
        last_beat = time.monotonic()
        yield _format_sse(tick)


def create_router(ctx: ObservabilityContext) -> APIRouter:
    router = APIRouter()

    @router.get("/events")
    async def eval_events(request: Request, run_id: str, last_seq: int = 0):
        emitter = get_emitter()
        last_seq_hdr = request.headers.get("last-event-id") or request.headers.get("Last-Event-ID")
        if last_seq_hdr:
            try:
                last_seq = int(last_seq_hdr)
            except ValueError:
                pass
        if not run_id:
            raise HTTPException(status_code=400, detail="run_id is required")
        return StreamingResponse(
            _event_stream(emitter, run_id, last_seq),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
