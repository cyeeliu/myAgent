"use client";
import type { Transport, AgentEvent, AgentClientMsg } from "../types";

// WebSocket transport: bidirectional. `send` writes JSON to the socket.
// On connect, sends a `resume` with the last seen seq so the server replays
// missed buffered events (spec §4.1 / §7).
export class WebSocketTransport implements Transport {
  private ws: WebSocket | null = null;
  private cb: ((e: AgentEvent) => void) | null = null;
  private url: string;
  private _lastSeq = 0;
  public onDowngrade: (() => void) | null = null;  // called when WS fails → SSE

  constructor(url: string) { this.url = url; }

  get lastSeq() { return this._lastSeq; }

  async connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.url);
      this.ws = ws;
      ws.onopen = () => {
        if (this._lastSeq > 0) ws.send(JSON.stringify({ type: "resume", last_seq: this._lastSeq }));
        resolve();
      };
      ws.onmessage = (ev) => {
        try {
          const e: AgentEvent = JSON.parse(ev.data);
          if (e.seq > 0) this._lastSeq = Math.max(this._lastSeq, e.seq);
          if (e.kind === "ping") return;
          this.cb?.(e);
        } catch { /* ignore malformed */ }
      };
      ws.onerror = () => {
        if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
          this.onDowngrade?.();
          reject(new Error("ws failed"));
        }
      };
      ws.onclose = () => { this.ws = null; };
    });
  }

  disconnect() { this.ws?.close(); this.ws = null; }
  send(msg: AgentClientMsg) { this.ws?.send(JSON.stringify(msg)); }
  onEvent(cb: (e: AgentEvent) => void) { this.cb = cb; }
}
