"""Event pipe: the channel between the agent worker (producer) and the WS/SSE
pump (consumer). Two implementations behind one interface:

- InMemoryPipe  — queue.Queue + deque (no Redis; local dev fallback).
- RedisStreamPipe — Redis Streams (hot event log, 24h TTL, survives gateway
  restart, multi-replica-ready). Stream id = integer seq, so XRANGE (N + is
  exactly "events with seq > N" — the existing last_seq resume model maps 1:1.

Postgres holds the cold, full message history (db.py); Redis holds the hot,
per-event stream for live replay. When a stream has expired but Postgres still
has the session, the hydrate path re-seeds the stream from history (synthesize).
"""
from __future__ import annotations
import asyncio
import json
import queue as _queue
import threading
from collections import deque
from typing import AsyncIterator, Optional

# Redis clients are module-level so all pipes share one pool. None when REDIS_URL
# is unset (InMemoryPipe path).
_sync_r = None
_async_r = None

STREAM_TTL = 24 * 3600          # 24h hot window; Postgres keeps the rest.
MAX_BUFFER_EVENTS = 1000        # InMemoryPipe cap (spec §7).
TTL_REFRESH_EVERY = 64          # refresh stream TTL every N publishes.


def _seq_of(entry_id) -> int:
    """Redis stream id is 'N-0' when XADD'd with id=str(N); pull the int."""
    return int(str(entry_id).split("-", 1)[0])


def init_redis(url: Optional[str]) -> None:
    """Open the shared sync + async Redis clients. No-op if url is None."""
    global _sync_r, _async_r
    if not url or _sync_r is not None:
        return
    import redis
    import redis.asyncio as aioredis
    _sync_r = redis.Redis.from_url(url, decode_responses=True)
    _async_r = aioredis.Redis.from_url(url, decode_responses=True)


async def close_redis() -> None:
    global _sync_r, _async_r
    if _sync_r is not None:
        _sync_r.close()
        _sync_r = None
    if _async_r is not None:
        await _async_r.aclose()
        _async_r = None


def redis_enabled() -> bool:
    return _sync_r is not None


class EventPipe:
    """Interface. Frames are dicts {seq, kind, payload}."""

    def publish(self, seq: int, kind: str, payload: dict) -> None: ...
    def replay_since(self, last_seq: int) -> list[dict]: ...
    async def live(self, after_seq: int) -> AsyncIterator[Optional[dict]]: ...
    def seed(self, frames: list[dict]) -> None: ...
    def count(self) -> int: ...


class InMemoryPipe(EventPipe):
    """queue.Queue + deque replay buffer (the original in-proc design)."""

    def __init__(self):
        self._q: "_queue.Queue" = _queue.Queue()
        self._buf: deque = deque(maxlen=MAX_BUFFER_EVENTS)
        self._lock = threading.Lock()

    def publish(self, seq: int, kind: str, payload: dict) -> None:
        frame = {"seq": seq, "kind": kind, "payload": payload}
        with self._lock:
            self._buf.append(frame)
        self._q.put(frame)

    def replay_since(self, last_seq: int) -> list[dict]:
        with self._lock:
            return [f for f in self._buf if f["seq"] > last_seq]

    async def live(self, after_seq: int) -> AsyncIterator[Optional[dict]]:
        last = after_seq
        while True:
            try:
                frame = self._q.get_nowait()
            except _queue.Empty:
                yield None
                await asyncio.sleep(0.05)
                continue
            if frame["seq"] <= last:
                continue
            last = frame["seq"]
            yield frame

    def seed(self, frames: list[dict]) -> None:
        with self._lock:
            for f in frames:
                self._buf.append(f)

    def count(self) -> int:
        with self._lock:
            return len(self._buf)


class RedisStreamPipe(EventPipe):
    """Redis Streams-backed pipe. Stream key = stream:{sid}. ID = str(seq)."""

    def __init__(self, sid: str):
        self._key = f"stream:{sid}"
        self._pub_count = 0
        self._pub_lock = threading.Lock()

    def publish(self, seq: int, kind: str, payload: dict) -> None:
        _sync_r.xadd(self._key, {"kind": kind, "payload": json.dumps(payload)}, id=str(seq))
        with self._pub_lock:
            self._pub_count += 1
            refresh = self._pub_count % TTL_REFRESH_EVERY == 0
        if refresh:
            _sync_r.expire(self._key, STREAM_TTL)

    def replay_since(self, last_seq: int) -> list[dict]:
        # XRANGE with min="(N" → exclusive of N → seq > N.
        out = []
        for entry_id, fields in _sync_r.xrange(self._key, min=f"({last_seq}"):
            out.append({"seq": _seq_of(entry_id), "kind": fields["kind"],
                        "payload": json.loads(fields["payload"])})
        return out

    async def live(self, after_seq: int) -> AsyncIterator[Optional[dict]]:
        last = str(after_seq)
        while True:
            # XREAD BLOCK 2000 → returns None on timeout; we yield None so the
            # pump can emit heartbeats without a separate timer.
            res = await _async_r.xread({self._key: last}, block=2000, count=100)
            if not res:
                yield None
                continue
            for _key, entries in res:
                for entry_id, fields in entries:
                    last = entry_id
                    yield {"seq": _seq_of(entry_id), "kind": fields["kind"],
                           "payload": json.loads(fields["payload"])}

    def seed(self, frames: list[dict]) -> None:
        if not frames:
            return
        pipe = _sync_r.pipeline()
        for f in frames:
            pipe.xadd(self._key, {"kind": f["kind"], "payload": json.dumps(f["payload"])},
                      id=str(f["seq"]))
        pipe.expire(self._key, STREAM_TTL)
        pipe.execute()

    def count(self) -> int:
        return _sync_r.xlen(self._key)


def make_pipe(sid: str) -> EventPipe:
    """Pick the pipe implementation based on whether Redis is initialized."""
    if redis_enabled():
        return RedisStreamPipe(sid)
    return InMemoryPipe()
