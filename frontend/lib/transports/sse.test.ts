import { describe, it, expect, beforeEach, vi } from "vitest";
import { SSETransport } from "./sse";

const MockEventSource = (globalThis as any).__MockEventSource as any;
const fetchMock = (globalThis as any).fetch as ReturnType<typeof vi.fn>;

describe("SSETransport", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    fetchMock.mockReset();
  });

  it("connects and dispatches named-event kinds with seq tracking", async () => {
    const t = new SSETransport("http://x/e", "http://x/s");
    const events: any[] = [];
    t.onEvent((e) => events.push(e));
    const p = t.connect();
    const es = MockEventSource.instances[0];
    es._open();
    await p;
    es._dispatch("token", JSON.stringify({ seq: 2, kind: "token", payload: { text: "a" } }));
    es._dispatch("tool_start", JSON.stringify({ seq: 3, kind: "tool_start", payload: { id: "t1", name: "bash" } }));
    expect(events.map((e) => e.kind)).toEqual(["token", "tool_start"]);
    expect(t.lastSeq).toBe(3);
  });

  it("send routes user_message / permission_response / interrupt to REST POSTs", async () => {
    fetchMock.mockResolvedValue({ ok: true });
    const t = new SSETransport("http://x/e", "http://x/s");
    t.onEvent(() => {});
    const p = t.connect();
    MockEventSource.instances[0]._open();
    await p;

    await t.send({ type: "user_message", text: "hi" });
    await t.send({ type: "permission_response", request_id: "r1", allow: true });
    await t.send({ type: "interrupt" });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[0][0]).toBe("http://x/s/messages");
    expect(fetchMock.mock.calls[1][0]).toBe("http://x/s/permissions/r1/respond");
    expect(fetchMock.mock.calls[2][0]).toBe("http://x/s/interrupt");
  });
});
