"""Gateway integration tests (spec §8): WS pump, SSE stream, permission flow,
Last-Event-ID replay — all against FastAPI TestClient with a scripted LLM."""
import os
os.environ.setdefault("MODEL_ID", "test-model")
os.environ.setdefault("OPENAI_API_KEY", "dummy")

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import threading
import time as _time
import code
import agent_core.adapter
import httpx
import uvicorn
from code import _TextBlock, _ToolUseBlock, SimpleNamespace
from fastapi.testclient import TestClient
from agent_gateway.main import app
from agent_gateway.sessions import manager

client = TestClient(app)

# Real uvicorn server for SSE streaming tests (TestClient.stream can't drive an
# infinite StreamingResponse generator cleanly).
_server_thread = None
_base_url = None


def _ensure_server():
    global _server_thread, _base_url
    if _server_thread is not None:
        return _base_url
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error")
    server = uvicorn.Server(config)
    _server_thread = threading.Thread(target=server.run, daemon=True)
    _server_thread.start()
    # wait for the socket
    for _ in range(100):
        try:
            with httpx.Client() as c:
                c.get("http://127.0.0.1:8765/api/skills", timeout=0.5)
            break
        except Exception:
            _time.sleep(0.05)
    _base_url = "http://127.0.0.1:8765"
    return _base_url


def _install_script(script):
    """Make code.chat_create pop scripted Anthropic-shaped responses.
    Emits token events when stream=True so the WS/SSE token path is exercised."""
    def fake(model, system=None, messages=None, tools=None,
             max_tokens=8000, stream=False, events=None):
        resp = script.pop(0)
        if stream and events is not None and resp.content:
            for block in resp.content:
                if getattr(block, "type", None) == "text" and block.text:
                    events.emit("token", {"text": block.text})
        return resp
    agent_core.adapter.chat_create = fake


def _resp(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


def test_create_session():
    r = client.post("/api/sessions", json={"transport": "auto"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "session_id" in data and data["transport"] in ("ws", "sse")
    print("test_create_session: OK", data)


def test_ws_text_turn():
    _install_script([_resp(_TextBlock("hello"), stop="end_turn")])
    sid = client.post("/api/sessions", json={"transport": "ws"}).json()["session_id"]
    with client.websocket_connect(f"/api/sessions/{sid}") as ws:
        ws.send_json({"type": "user_message", "text": "hi"})
        frames = []
        while True:
            f = ws.receive_json()
            frames.append(f)
            if f["kind"] == "done":
                break
    kinds = [f["kind"] for f in frames]
    assert "token" in kinds, kinds
    assert kinds[-1] == "done", kinds
    assert frames[0]["seq"] == 1 and frames[-1]["seq"] > 0, frames
    print("test_ws_text_turn: OK", kinds)


def test_ws_permission_flow():
    # Bash destructive → permission_request → grant via WS → tool_result → done.
    _install_script([
        _resp(_ToolUseBlock(id="t1", name="bash",
                            input={"command": "rm /tmp/gw_x"}), stop="tool_use"),
        _resp(_TextBlock("ok"), stop="end_turn"),
    ])
    sid = client.post("/api/sessions", json={"transport": "ws"}).json()["session_id"]
    with client.websocket_connect(f"/api/sessions/{sid}") as ws:
        ws.send_json({"type": "user_message", "text": "rm something"})
        # Expect: tool_start, permission_request, then we grant.
        seen = []
        rid = None
        while True:
            f = ws.receive_json()
            seen.append(f)
            if f["kind"] == "permission_request":
                rid = f["payload"].get("request_id") or f["payload"].get("rid")
                # Find the pending request id from the session.
                gs = manager.get(sid)
                rid = next(iter(gs.pending_permissions)) if gs.pending_permissions else rid
                ws.send_json({"type": "permission_response", "request_id": rid, "allow": True})
            if f["kind"] == "done":
                break
    kinds = [f["kind"] for f in seen]
    assert "permission_request" in kinds, kinds
    assert "tool_result" in kinds, kinds
    print("test_ws_permission_flow: OK", kinds)


def test_sse_stream_and_replay():
    base = _ensure_server()
    _install_script([_resp(_TextBlock("sse hello"), stop="end_turn")])
    sid = httpx.post(f"{base}/api/sessions", json={"transport": "sse"}).json()["session_id"]

    # POST a user message to start the turn.
    r = httpx.post(f"{base}/api/sessions/{sid}/messages", json={"text": "hi"})
    assert r.status_code == 200, r.text

    # Open the SSE stream and read until `event: done`.
    with httpx.stream("GET", f"{base}/api/sessions/{sid}/events",
                      headers={"Accept": "text/event-stream"}, timeout=10) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = b""
        for chunk in resp.iter_bytes():
            body += chunk
            if b"event: done" in body:
                break
    text = body.decode()
    assert "event: token" in text, text
    assert "event: done" in text, text
    assert "id: 1" in text, text
    print("test_sse_stream_and_replay: OK (stream)")

    # Replay: reconnect with Last-Event-ID; buffered frames with seq > last are resent.
    gs = manager.get(sid)
    last_seq = gs.agent._seq - 1
    with httpx.stream("GET", f"{base}/api/sessions/{sid}/events",
                      headers={"Last-Event-ID": str(last_seq)}, timeout=10) as resp:
        body = b""
        for chunk in resp.iter_bytes():
            body += chunk
            if b"event: done" in body or len(body) > 2000:
                break
    text = body.decode()
    assert f"id: {last_seq + 1}" in text, (last_seq, text)
    print("test_sse_stream_and_replay: OK (replay)")


def test_sse_permission_post():
    base = _ensure_server()
    _install_script([
        _resp(_ToolUseBlock(id="t1", name="bash",
                            input={"command": "rm /tmp/gw_sse_x"}), stop="tool_use"),
        _resp(_TextBlock("ok"), stop="end_turn"),
    ])
    sid = httpx.post(f"{base}/api/sessions", json={"transport": "sse"}).json()["session_id"]
    httpx.post(f"{base}/api/sessions/{sid}/messages", json={"text": "rm something"})

    # Drain the SSE stream until permission_request, then grant via REST (once).
    with httpx.stream("GET", f"{base}/api/sessions/{sid}/events", timeout=10) as resp:
        body = b""
        rid = None
        granted = False
        for chunk in resp.iter_bytes():
            body += chunk
            if b"event: permission_request" in body and not granted:
                gs = manager.get(sid)
                for _ in range(100):
                    if gs.pending_permissions:
                        rid = next(iter(gs.pending_permissions))
                        break
                    _time.sleep(0.02)
                assert rid, "no pending permission"
                r = httpx.post(f"{base}/api/sessions/{sid}/permissions/{rid}/respond",
                               json={"allow": True})
                assert r.status_code == 200, r.text
                granted = True
            if b"event: done" in body:
                break
    assert b"event: permission_request" in body, body
    assert b"event: done" in body, body
    print("test_sse_permission_post: OK")


def test_status_and_views():
    sid = client.post("/api/sessions", json={"transport": "ws"}).json()["session_id"]
    r = client.get(f"/api/sessions/{sid}/status")
    assert r.status_code == 200 and r.json()["session_id"] == sid, r.text
    for path in ("/api/skills", "/api/mcp"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text)
    print("test_status_and_views: OK")


if __name__ == "__main__":
    test_create_session()
    test_ws_text_turn()
    test_ws_permission_flow()
    test_sse_stream_and_replay()
    test_sse_permission_post()
    test_status_and_views()
    print("\nAll gateway tests passed.")
