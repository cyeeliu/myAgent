"""WebChannel — method-routed WebSocket channel at /ws (mirrors jiuwenswarm web_connect).

Wire protocol (single connection per client):
  client → server: {type:"req", id, method, params}
  server → client: {type:"res", id, ok, payload, error?}
                 | {type:"event", event, payload, seq?, stream_id?}

Inbound: parse req → decode_envelope → MessageHandler.handle_inbound →
  agent_compat → AgentResponse → send res.
Outbound: the session's EventPipe is drained per-connection and each frame is
  mapped (gateway_push.wire) to an event frame and sent. This is the live
  token/tool/permission stream for the session bound to the connection.

A connection binds to a session_id (from chat.send / session.create params or
the ?session_id= query). Reconnect resumes via ?last_seq=N.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect, Query

from ..base import BaseChannel, ChannelMetadata
from ...common.e2a import decode_envelope, encode_response, encode_error, parse_frame
from ...common.e2a.gateway_normalize import e2a_from_channel_request
from ...common.e2a.agent_compat import execute_agent_request
from ...common.schema.agent import AgentResponse
from ...common.schema.message import ReqMethod
from ...gateway_push.wire import frame_to_event
from ...debug import debug

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 15.0


@dataclass
class WebChannelConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    path: str = "/ws"
    allow_origins: list[str] = field(default_factory=lambda: ["*"])


class WebChannel(BaseChannel):
    """Web WS channel. `mount(app)` registers the /ws route on the FastAPI app."""

    def __init__(self, config: WebChannelConfig, sessions, message_handler=None):
        super().__init__(ChannelMetadata(
            channel_id="web",
            channel_type="web",
            enabled=config.enabled,
            host=config.host,
            port=config.port,
            path=config.path,
        ))
        self.config = config
        self.sessions = sessions
        self.message_handler = message_handler
        self._app = None

    def set_message_handler(self, handler) -> None:
        self.message_handler = handler

    async def start(self) -> None:
        # The WS endpoint is mounted on the FastAPI app via mount(app); nothing
        # to start here. Mark started so ChannelManager is consistent.
        self._started = True

    def mount(self, app) -> None:
        self._app = app

        @app.websocket(self.config.path)
        async def ws_endpoint(ws: WebSocket,
                              session_id: Optional[str] = Query(default=None),
                              last_seq: int = Query(default=0, ge=0),
                              # jiuwenswarm frontend sends these on connect; we accept
                              # and ignore them (myAgent config is server-side, not per-conn).
                              provider: Optional[str] = Query(default=None),
                              api_key: Optional[str] = Query(default=None),
                              api_base: Optional[str] = Query(default=None),
                              model: Optional[str] = Query(default=None),
                              project_path: Optional[str] = Query(default=None)):
            await self._handle_connection(ws, session_id, last_seq)

    async def _handle_connection(self, ws: WebSocket, session_id: Optional[str],
                                 last_seq: int) -> None:
        await ws.accept()
        debug("connect sid=%r last_seq=%r", session_id, last_seq)
        loop = asyncio.get_running_loop()
        # Connection state held in a mutable holder so _receiver can bind the
        # session's event drain mid-connection (the jiuwenswarm frontend keeps a
        # single socket with no ?session_id= and sends session_id inside
        # chat.send / session.create params; the drain must start when those
        # arrive, not only at connect time).
        ctx = {"bound_sid": session_id, "sender_task": None, "last_seq": last_seq}
        # jiuwenswarm frontend waits for connection.ack (or legacy hello) before
        # marking the socket ready and issuing config.get/models.list/session.list.
        # Emit it immediately after accept so the on-connect bootstrap proceeds.
        try:
            await ws.send_json({
                "type": "event",
                "event": "connection.ack",
                "payload": {
                    "session_id": session_id or "",
                    "mode": "agent",
                    "tools": [],
                    "protocol_version": "1",
                },
            })
        except Exception:
            logger.exception("web channel failed to send connection.ack")
        # Hydrate the session up-front if one was given so outbound drain starts.
        if session_id:
            gs = await asyncio.to_thread(self.sessions.get_or_hydrate, session_id, loop)
            if gs is not None:
                ctx["sender_task"] = asyncio.create_task(
                    self._drain_session(ws, gs, last_seq, self._make_outbound(ws)))

        try:
            await self._receiver(ws, loop, ctx)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("web channel connection error")
        finally:
            t = ctx.get("sender_task")
            if t is not None:
                t.cancel()

    def _make_outbound(self, ws: WebSocket):
        async def outbound_sub(frame: dict[str, Any]) -> None:
            try:
                await ws.send_json(frame)
            except Exception:
                pass
        return outbound_sub

    async def _ensure_drain(self, ws: WebSocket, loop, ctx, sid: str) -> None:
        """Bind this connection to `sid`'s event pipe and start draining if we
        haven't already. Called when chat.send / session.create arrives with a
        session_id on a connection that wasn't bound at connect time."""
        debug("_ensure_drain sid=%r bound_sid=%r has_task=%s last_seq=%r",
              sid, ctx.get("bound_sid"), ctx.get("sender_task") is not None, ctx.get("last_seq"))
        if not sid or sid == ctx.get("bound_sid") and ctx.get("sender_task") is not None:
            debug("_ensure_drain early-return(1) sid=%r", sid)
            return
        gs = await asyncio.to_thread(self.sessions.get_or_hydrate, sid, loop)
        if gs is None:
            debug("_ensure_drain no-session sid=%r", sid)
            return
        # If already draining this same session, nothing to do.
        if ctx.get("bound_sid") == sid and ctx.get("sender_task") is not None:
            debug("_ensure_drain already-bound sid=%r", sid)
            return
        # Cancel any previous drain (session switch) before starting a new one.
        prev = ctx.get("sender_task")
        if prev is not None:
            prev.cancel()
        ctx["bound_sid"] = sid
        # Start at THIS session's live tail. ctx["last_seq"] is only meaningful
        # for the session it was captured for (reconnect resume via ?last_seq=,
        # which is handled at connect, not here). Reusing it after a session
        # switch would skip the new session's frames — the new session emits
        # starting at seq 1, but a stale last_seq=N (N>0) makes live(N) drop
        # every frame ≤ N, so chat.delta/chat.final never reach the client and
        # the UI spinner never clears.
        start_seq = getattr(gs.agent, "_seq", 0)
        ctx["last_seq"] = start_seq
        ctx["sender_task"] = asyncio.create_task(
            self._drain_session(ws, gs, start_seq, self._make_outbound(ws)))
        debug("_ensure_drain started drain sid=%r last_seq=%r", sid, start_seq)

    async def _ensure_drain_live(self, ws: WebSocket, loop, ctx, sid: str) -> None:
        """Bind this connection to `sid`'s event pipe at the LIVE TAIL (no replay)
        so events emitted from now on are delivered without re-streaming the
        session's synthesized replay frames. Used by history.get: the structured
        conversation is delivered via history.message events emitted by the
        handler, not via the pipe's replay buffer — replaying here would
        double-render the prior turn as live chat.delta/final frames."""
        if not sid:
            return
        gs = await asyncio.to_thread(self.sessions.get_or_hydrate, sid, loop)
        if gs is None:
            debug("_ensure_drain_live no-session sid=%r", sid)
            return
        # If already draining this same session, leave it — new events will flow.
        if ctx.get("bound_sid") == sid and ctx.get("sender_task") is not None:
            debug("_ensure_drain_live already-bound sid=%r", sid)
            return
        prev = ctx.get("sender_task")
        if prev is not None:
            prev.cancel()
        ctx["bound_sid"] = sid
        # Start at the last assigned seq so live() only yields frames published
        # after this point (the history.message events the handler is about to
        # emit), skipping the replay loop entirely.
        start_seq = getattr(gs.agent, "_seq", 0)
        ctx["last_seq"] = start_seq
        ctx["sender_task"] = asyncio.create_task(
            self._drain_session(ws, gs, start_seq, self._make_outbound(ws)))
        debug("_ensure_drain_live started drain sid=%r start_seq=%r", sid, start_seq)

    async def _receiver(self, ws: WebSocket, loop, ctx):
        """Inbound loop: parse req → dispatch → send res."""
        bound_sid = ctx.get("bound_sid")
        outbound_sub = self._make_outbound(ws)
        while True:
            raw = await ws.receive_text()
            obj = parse_frame(raw)
            if obj is None:
                await ws.send_json(encode_error("", "invalid json"))
                continue
            if obj.get("type") != "req":
                # Legacy client→server control frames (user_message/interrupt/…)
                # are accepted for backward compat and routed to chat methods.
                await self._handle_legacy(ws, obj, loop, bound_sid)
                continue
            env = decode_envelope(obj, channel_id="web")
            if env is None:
                # Methods the frontend calls that myAgent has no backend for
                # (media.persist,
                # permissions.tools.get, cron.*, updater.*, extensions.*,
                # harness.*, channel.*.get_conf, …). Return a no-op success so
                # the frontend's kept panels don't throw; dropped panels never
                # fire these because their nav entries are feature-flagged off.
                await ws.send_json({
                    "type": "res", "id": str(obj.get("id", "")),
                    "ok": True, "payload": {},
                })
                continue
            req = e2a_from_channel_request(env)
            debug("req method=%r sid=%r", env.method.value, getattr(req, "session_id", None))
            # history.get streams the conversation as history.message events on
            # the session pipe; bind the drain to that session at the LIVE TAIL
            # before executing so those events reach the client (and without
            # replaying the session's synthesized frames, which would
            # double-render the prior turn as live chat frames).
            if env.method == ReqMethod.HISTORY_GET and req.session_id:
                await self._ensure_drain_live(ws, loop, ctx, req.session_id)
            try:
                resp = await execute_agent_request(req, sessions=self.sessions)
            except Exception as e:
                logger.exception("agent_compat error for %s", env.method.value)
                resp = AgentResponse(request_id=env.request_id, ok=False,
                                     error=f"{type(e).__name__}: {e}")
            await ws.send_json(encode_response(resp))
            debug("res id=%r ok=%s method=%r", env.request_id, resp.ok, env.method.value)
            # history.get for a session that doesn't exist on the backend (e.g.
            # the frontend's client-generated fallback sess_ id before any
            # chat.send has self-healed it). There's no session pipe to carry
            # the status:'done' frame, so emit it directly on the ws so the
            # frontend's beginHistoryRestore finalizes (onEmpty) and clears
            # isLoadingHistory — otherwise the next chat.final can't clear the
            # three-dots spinner (its setProcessing(false) is gated on
            # !isLoadingHistory).
            if env.method == ReqMethod.HISTORY_GET and isinstance(resp.payload, dict) \
                    and resp.payload.get("empty"):
                await ws.send_json({
                    "type": "event",
                    "event": "history.message",
                    "payload": {
                        "status": "done",
                        "session_id": req.session_id,
                        "page_idx": resp.payload.get("page_idx", 1),
                        "total_pages": 1,
                    },
                })
            # Bind the event drain when a session becomes active on this socket.
            # chat.send carries session_id in the envelope; session.create returns
            # the new session_id in its payload. Without this, tokens emitted by
            # the worker thread never reach a client that connected without
            # ?session_id= (the jiuwenswarm frontend's default mode).
            if env.method == ReqMethod.CHAT_SEND and req.session_id:
                await self._ensure_drain(ws, loop, ctx, req.session_id)
            elif env.method == ReqMethod.SESSION_CREATE:
                new_sid = (resp.payload or {}).get("session_id") if isinstance(resp.payload, dict) else None
                if isinstance(new_sid, str) and new_sid:
                    await self._ensure_drain(ws, loop, ctx, new_sid)

    async def _handle_legacy(self, ws: WebSocket, obj: dict, loop,
                             bound_sid: Optional[str]):
        """Backward-compat: old {type:'user_message'|'interrupt'|'permission_response'} frames."""
        mtype = obj.get("type")
        if not bound_sid:
            return
        gs = self.sessions.get(bound_sid)
        if gs is None:
            return
        if mtype == "user_message":
            ok = gs.post_message(obj.get("text", ""))
            if not ok:
                await ws.send_json(encode_error("", "a turn is already in flight"))
        elif mtype == "interrupt":
            gs.interrupt()
        elif mtype == "permission_response":
            gs.grant(obj.get("request_id", ""), obj.get("allow", False), obj.get("modify"))

    async def _drain_session(self, ws: WebSocket, gs, last_seq: int, outbound_sub):
        """Replay missed frames then drain the live EventPipe, mapping each to a
        jiuwenswarm-style event frame. Heartbeat on silence."""
        sid = gs.session_id
        debug("drain start sid=%r last_seq=%r", sid, last_seq)
        try:
            last = last_seq
            _replay = gs.pipe.replay_since(last_seq)
            debug("drain replay sid=%r count=%d", sid, len(_replay))
            for frame in _replay:
                # Skip ephemeral streaming kinds on replay. The frontend reconstructs
                # past messages from history.get, not from re-streamed deltas; feeding
                # old token frames as chat.delta would append them to the LIVE stream
                # buffer and corrupt the current response (e.g. a prior turn's text
                # gets prepended to the new reply). `token` (chat.delta) and `text`
                # (chat.notice) are per-turn streaming artifacts — only the live drain
                # below should forward them. Discrete events (tool_start/tool_result/
                # done/user/…) are kept for replay so a reconnect can resync state.
                if frame.get("kind") in ("token", "text"):
                    last = max(last, frame.get("seq", last))
                    continue
                ev = frame_to_event(frame)
                if ev is not None:
                    ev["payload"] = {**ev.get("payload", {}), "replay": True}
                    await outbound_sub(ev)
                    debug("drain>>replay sid=%r kind=%r seq=%r ev=%r",
                          sid, frame.get("kind"), frame.get("seq"), ev.get("event"))
                    last = max(last, frame.get("seq", last))
            last_beat = time.monotonic()
            async for tick in gs.pipe.live(last):
                if tick is None:
                    if time.monotonic() - last_beat >= HEARTBEAT_INTERVAL:
                        await outbound_sub({"type": "event", "event": "heartbeat",
                                            "payload": {"t": time.time()}})
                        last_beat = time.monotonic()
                        gs.last_activity = time.time()
                    continue
                ev = frame_to_event(tick)
                if ev is not None:
                    # Tag chat.final with the session_id so the frontend's
                    # `streamId && payloadSessionId` finalization branch fires and
                    # calls stopStreaming() — otherwise currentStreamId stays set
                    # and the next turn's deltas append to this turn's bubble. Our
                    # done payload carries no content (the streamed buffer already
                    # holds it), so this only flips isStreaming=false.
                    # team.* events get the same tag so shouldHandleSessionEvent
                    # routes them to the correct session's TeamArea in multi-tab.
                    if ev.get("event") in ("chat.final", "team.member",
                                           "team.task", "team.event",
                                           "team.message"):
                        ev["payload"] = {**ev.get("payload", {}),
                                         "session_id": sid}
                    await outbound_sub(ev)
                    debug("drain>>live sid=%r kind=%r seq=%r ev=%r",
                          sid, tick.get("kind"), tick.get("seq"), ev.get("event"))
                gs.last_activity = time.time()
                last_beat = time.monotonic()
        except Exception:
            debug("drain EXITED sid=%r", sid)
            logger.exception("drain exited sid=%r", sid)
