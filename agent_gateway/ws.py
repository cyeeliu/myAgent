"""WebSocket handler: bidirectional channel for a session.

Server → client: agent event frames {seq, kind, payload} (token, text,
tool_start, tool_result, permission_request, error, compacted, done) + ping.
Client → server: {type: "user_message"|"permission_response"|"interrupt"|"resume", ...}

Reconnect: client sends {type:"resume", last_seq: N} as the first message;
server replays buffered frames with seq > N then continues the live drain.
"""
from __future__ import annotations
import asyncio
import json
import queue as _queue
import time

from fastapi import WebSocket

HEARTBEAT_INTERVAL = 15.0
POLL_INTERVAL = 0.05


async def _sender(ws: WebSocket, session, last_seq: int):
    """Drain the session's thread-safe queue and send frames.
    Skips frames already replayed from the buffer. Polls with get_nowait so
    cancellation is prompt (no executor thread blocking on get)."""
    q = session.queue
    last_beat = time.monotonic()
    while True:
        try:
            frame = q.get_nowait()
        except _queue.Empty:
            if time.monotonic() - last_beat >= HEARTBEAT_INTERVAL:
                await ws.send_json({"seq": 0, "kind": "ping",
                                    "payload": {"t": time.time()}})
                last_beat = time.monotonic()
            await asyncio.sleep(POLL_INTERVAL)
            continue
        seq = frame.get("seq", 0)
        if seq and seq <= last_seq:
            continue  # already replayed from buffer; skip
        last_seq = max(last_seq, seq)
        await ws.send_json(frame)


async def _receiver(ws: WebSocket, session):
    """Handle client → server messages for the life of the connection."""
    while True:
        raw = await ws.receive_text()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send_json({"seq": 0, "kind": "error",
                                "payload": {"error": "invalid json"}})
            continue
        mtype = msg.get("type")
        if mtype == "user_message":
            ok = session.post_message(msg.get("text", ""))
            if not ok:
                await ws.send_json({"seq": 0, "kind": "error",
                                    "payload": {"error": "a turn is already in flight"}})
        elif mtype == "permission_response":
            session.grant(msg.get("request_id", ""), msg.get("allow", False),
                          msg.get("modify"))
        elif mtype == "interrupt":
            session.interrupt()
        elif mtype == "resume":
            # Resume is handled at connect; late resume messages just update state.
            pass
        else:
            await ws.send_json({"seq": 0, "kind": "error",
                                "payload": {"error": f"unknown type {mtype}"}})


async def handle_ws(ws: WebSocket, session, last_seq: int = 0):
    await ws.accept()
    # Replay buffered events missed during reconnect, then run sender + receiver.
    for frame in session.snapshot_since(last_seq):
        await ws.send_json(frame)
    last = last_seq
    sender = asyncio.create_task(_sender(ws, session, last))
    receiver = asyncio.create_task(_receiver(ws, session))
    try:
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (sender, receiver):
            t.cancel()
        # The agent worker thread is left to finish; its events stay buffered.
