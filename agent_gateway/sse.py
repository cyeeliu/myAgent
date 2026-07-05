"""SSE transport (spec §4.1).

GET /api/sessions/{id}/events  →  text/event-stream
  Frames:  id: <seq>\nevent: <kind>\ndata: <json payload>\n\n
  Reconnect: browser sends Last-Event-ID header automatically; server replays
  buffered frames with seq > last_seq then continues the live drain.
  Heartbeat: a `: ping` comment every 15s to keep proxies alive.

SSE is unidirectional; client → server (user messages, permission responses,
interrupts) goes via the REST POST endpoints in main.py under the same session.
"""
from __future__ import annotations
import asyncio
import json
import queue as _queue
import time
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse

HEARTBEAT_INTERVAL = 15.0
POLL_INTERVAL = 0.05


def _format_sse(frame: dict) -> str:
    seq = frame.get("seq", 0)
    kind = frame.get("kind", "message")
    payload = frame.get("payload", {})
    return f"id: {seq}\nevent: {kind}\ndata: {json.dumps(payload)}\n\n"


async def _event_stream(gs, last_seq: int):
    q = gs.queue
    # Replay missed events from the buffer.
    for frame in gs.snapshot_since(last_seq):
        yield _format_sse(frame)
        last_seq = max(last_seq, frame.get("seq", 0))
    # Live drain (poll — no executor thread blocking on get).
    last_beat = time.monotonic()
    while True:
        try:
            frame = q.get_nowait()
        except _queue.Empty:
            if time.monotonic() - last_beat >= HEARTBEAT_INTERVAL:
                yield ": ping\n\n"
                last_beat = time.monotonic()
                gs.last_activity = time.time()  # heartbeat keeps the session alive
            await asyncio.sleep(POLL_INTERVAL)
            continue
        seq = frame.get("seq", 0)
        if seq and seq <= last_seq:
            continue  # already replayed
        last_seq = max(last_seq, seq)
        gs.last_activity = time.time()
        yield _format_sse(frame)


def register(app: FastAPI, manager):
    @app.get("/api/sessions/{sid}/events")
    async def sse_events(sid: str, request: Request):
        gs = manager.get(sid)
        if gs is None:
            raise HTTPException(status_code=404, detail="session not found")
        # Browser sends Last-Event-ID automatically on reconnect.
        last_seq_hdr = request.headers.get("last-event-id") or request.headers.get("Last-Event-ID")
        last_seq = int(last_seq_hdr) if last_seq_hdr else 0
        gs.last_activity = time.time()
        return StreamingResponse(
            _event_stream(gs, last_seq),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
