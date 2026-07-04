"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentEvent, AgentClientMsg, Transport } from "./types";
import { WebSocketTransport } from "./transports/ws";
import { SSETransport } from "./transports/sse";

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8000";

export interface TransportState {
  transport: "ws" | "sse" | null;
  connected: boolean;
  send: (msg: AgentClientMsg) => void;
  interrupt: () => void;
}

// useAgentTransport: connect to an existing session (by id), pick transport
// (auto → WS, fall back to SSE), dispatch events to the supplied callback.
// Session creation/listing is handled by useSessionManager; this hook just
// attaches a transport to whichever session id it's given. Reconnects when
// sessionId changes; passes last_seq=0 so the server replays the session's
// buffered events and the reducer rebuilds the conversation.
export function useAgentTransport(
  sessionId: string | null,
  onEvent: (e: AgentEvent) => void,
): TransportState {
  const [transport, setTransport] = useState<"ws" | "sse" | null>(null);
  const [connected, setConnected] = useState(false);
  const transportRef = useRef<Transport | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    (async () => {
      // try WS, fall back to SSE
      const wsUrl = `${GATEWAY.replace(/^http/, "ws")}/api/sessions/${sessionId}?last_seq=0`;
      const ws = new WebSocketTransport(wsUrl);
      ws.onEvent((e) => onEventRef.current(e));
      try {
        await ws.connect();
        if (cancelled) { ws.disconnect(); return; }
        transportRef.current = ws;
        setTransport("ws");
        setConnected(true);
        return;
      } catch { /* fall through to SSE */ }

      const sse = new SSETransport(
        `${GATEWAY}/api/sessions/${sessionId}/events`,
        `${GATEWAY}/api/sessions/${sessionId}`,
      );
      sse.onEvent((e) => onEventRef.current(e));
      await sse.connect();
      if (cancelled) { sse.disconnect(); return; }
      transportRef.current = sse;
      setTransport("sse");
      setConnected(true);
    })();
    return () => {
      cancelled = true;
      transportRef.current?.disconnect();
      transportRef.current = null;
      setConnected(false);
      setTransport(null);
    };
  }, [sessionId]);

  const send = useCallback((msg: AgentClientMsg) => {
    transportRef.current?.send(msg);
  }, []);

  const interrupt = useCallback(() => {
    transportRef.current?.send({ type: "interrupt" });
  }, []);

  return { transport, connected, send, interrupt };
}
