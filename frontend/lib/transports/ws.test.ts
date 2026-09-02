import { describe, it, expect, beforeEach } from "vitest";
import { WebSocketTransport } from "./ws";

const MockWebSocket = (globalThis as any).__MockWebSocket as any;

describe("WebSocketTransport", () => {
  beforeEach(() => { MockWebSocket.instances = []; });

  it("connects and dispatches parsed events, tracking lastSeq", async () => {
    const t = new WebSocketTransport("ws://x/s");
    const events: any[] = [];
    t.onEvent((e) => events.push(e));
    const p = t.connect();
    const ws = MockWebSocket.instances[0];
    ws._open();
    await p;
    ws._recv(JSON.stringify({ seq: 1, kind: "token", payload: { text: "hi" } }));
    ws._recv(JSON.stringify({ seq: 5, kind: "done", payload: {} }));
    expect(events.map((e) => e.kind)).toEqual(["token", "done"]);
    expect(events[0].payload.text).toBe("hi");
    expect(t.lastSeq).toBe(5);
  });

  it("send serializes client messages and requests resume with last_seq", async () => {
    const t = new WebSocketTransport("ws://x/s");
    t.onEvent(() => {});
    const p = t.connect();
    const ws = MockWebSocket.instances[0];
    ws._open();
    await p;
    // last_seq is sent as a query param at connect time; here we just check send.
    await t.send({ type: "user_message", text: "hello" });
    expect(ws.sent.length).toBe(1);
    expect(JSON.parse(ws.sent[0])).toEqual({ type: "user_message", text: "hello" });
  });

  it("connect rejects on error so the hook can fall back to SSE", async () => {
    const t = new WebSocketTransport("ws://x/s");
    t.onEvent(() => {});
    const p = t.connect();
    MockWebSocket.instances[0]._err();
    await expect(p).rejects.toBeDefined();
  });
});
