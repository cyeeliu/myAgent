"""WebSocket handler: bidirectional channel for a session.

Server → client: agent event frames {seq, kind, payload} (token, text,
tool_start, tool_result, permission_request, error, compacted, done) + ping.
Client → server: {type: "user_message"|"permission_response"|"interrupt"|"resume", ...}

Reconnect: client sends ?last_seq=N on the WS url; server replays buffered
frames with seq > N (pipe.replay_since) then continues the live drain
(pipe.live). The pipe is either in-memory (queue+deque) or Redis Streams —
this handler is pipe-agnostic.
"""
from __future__ import annotations
import asyncio
import json
import time

from fastapi import WebSocket

HEARTBEAT_INTERVAL = 15.0


async def _sender(ws: WebSocket, session, last_seq: int):
    """Replay missed frames then drain live events from the pipe, forwarding to
    the WS. Emits a ping heartbeat every HEARTBEAT_INTERVAL of silence."""
    last_beat = time.monotonic()
    async for tick in session.pipe.live(last_seq):
        if tick is None:
            # idle tick (pipe.live yields None on its block-timeout)
            if time.monotonic() - last_beat >= HEARTBEAT_INTERVAL:
                await ws.send_json({"seq": 0, "kind": "ping",
                                    "payload": {"t": time.time()}})
                last_beat = time.monotonic()
                session.last_activity = time.time()
            continue
        await ws.send_json(tick)
        session.last_activity = time.time()
        last_beat = time.monotonic()


async def _receiver(ws: WebSocket, session):
    """Handle client → server messages for the life of the connection."""
    while True:
        raw = await ws.receive_text()
        session.last_activity = time.time()
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
    session.last_activity = time.time()
    # Replay buffered events missed during reconnect, then run sender + receiver.
    last = last_seq
    for frame in session.pipe.replay_since(last_seq):
        await ws.send_json(frame)
        last = frame["seq"]
    sender = asyncio.create_task(_sender(ws, session, last))
    receiver = asyncio.create_task(_receiver(ws, session))
    try:
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (sender, receiver):
            t.cancel()
        # The agent worker thread is left to finish; its events stay in the pipe.
