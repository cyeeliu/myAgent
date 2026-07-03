"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentEvent, AgentClientMsg, Transport } from "./types";
import { WebSocketTransport } from "./transports/ws";
import { SSETransport } from "./transports/sse";

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8000";

export interface TransportState {
  sessionId: string | null;
  transport: "ws" | "sse" | null;
  connected: boolean;
  send: (msg: AgentClientMsg) => void;
  interrupt: () => void;
}

// useAgentTransport: create a session, pick transport (auto → WS, fall back to
// SSE), dispatch events to the supplied callback. Reconnect on drop.
export function useAgentTransport(onEvent: (e: AgentEvent) => void): TransportState {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [transport, setTransport] = useState<"ws" | "sse" | null>(null);
  const [connected, setConnected] = useState(false);
  const transportRef = useRef<Transport | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 1. create session (auto)
      const r = await fetch(`${GATEWAY}/api/sessions`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transport: "auto" }),
      });
      const { session_id } = await r.json();
      if (cancelled) return;
      setSessionId(session_id);

      // 2. try WS, fall back to SSE
      const wsUrl = `${GATEWAY.replace(/^http/, "ws")}/api/sessions/${session_id}`;
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
        `${GATEWAY}/api/sessions/${session_id}/events`,
        `${GATEWAY}/api/sessions/${session_id}`,
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
    };
  }, []);

  const send = useCallback((msg: AgentClientMsg) => {
    transportRef.current?.send(msg);
  }, []);

  const interrupt = useCallback(() => {
    transportRef.current?.send({ type: "interrupt" });
  }, []);

  return { sessionId, transport, connected, send, interrupt };
}
