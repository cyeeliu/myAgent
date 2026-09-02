"use client";
import type { Transport, AgentEvent, AgentClientMsg } from "../types";

// SSE transport: unidirectional server→client via EventSource. Client→server
// (user_message, permission_response, interrupt) goes via REST POSTs to the
// gateway under the same session (spec §4.1). Reconnect/resume is native:
// the browser auto-sends Last-Event-ID.
export class SSETransport implements Transport {
  private es: EventSource | null = null;
  private cb: ((e: AgentEvent) => void) | null = null;
  private eventsUrl: string;
  private postBase: string;     // e.g. http://host:port/api/sessions/{id}
  private _lastSeq = 0;

  constructor(eventsUrl: string, postBase: string) {
    this.eventsUrl = eventsUrl;
    this.postBase = postBase;
  }

  get lastSeq() { return this._lastSeq; }

  async connect(): Promise<void> {
    return new Promise((resolve) => {
      const es = new EventSource(this.eventsUrl);
      this.es = es;
      es.onopen = () => resolve();
      es.onmessage = (ev) => this.dispatch("text", ev.data);
      for (const kind of ["token", "text", "tool_start", "tool_result",
                          "error", "permission_request", "compacted", "done"] as const) {
        es.addEventListener(kind, (ev: MessageEvent) => this.dispatch(kind, ev.data));
      }
    });
  }

  private dispatch(kind: string, data: string) {
    try {
      const payload = JSON.parse(data);
      const seq = payload.seq ?? 0;
      if (seq > 0) this._lastSeq = Math.max(this._lastSeq, seq);
      this.cb?.({ seq, kind: kind as AgentEvent["kind"], payload });
    } catch { /* ignore */ }
  }

  disconnect() { this.es?.close(); this.es = null; }

  async send(msg: AgentClientMsg): Promise<void> {
    // SSE can't carry client→server; use REST POSTs.
    const base = this.postBase;
    if (msg.type === "user_message") {
      await fetch(`${base}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: msg.text }) });
    } else if (msg.type === "permission_response") {
      await fetch(`${base}/permissions/${msg.request_id}/respond`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ allow: msg.allow, modify: msg.modify }) });
    } else if (msg.type === "interrupt") {
      await fetch(`${base}/interrupt`, { method: "POST" });
    }
  }

  onEvent(cb: (e: AgentEvent) => void) { this.cb = cb; }
}
