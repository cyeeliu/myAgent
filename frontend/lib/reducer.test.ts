import { describe, it, expect } from "vitest";
import { reduce, reduceUser, initialState, applyLiveActivity, initialActivity } from "./reducer";

const ev = (kind: any, payload: any, seq = 1) => ({ kind, payload, seq });

describe("reducer (items)", () => {
  it("accumulates tokens into one assistant bubble", () => {
    let s = initialState();
    s = reduce(s, ev("token", { text: "Hel" }, 1));
    s = reduce(s, ev("token", { text: "lo" }, 2));
    expect(s.items).toHaveLength(1);
    expect(s.items[0]).toMatchObject({ kind: "assistant", text: "Hello" });
    expect(s.curAssistant).toBe(0);
  });

  it("tool_start then tool_result fills the card", () => {
    let s = initialState();
    s = reduce(s, ev("token", { text: "running" }, 1));
    s = reduce(s, ev("tool_start", { id: "t1", name: "bash", input: { command: "ls" } }, 2));
    s = reduce(s, ev("tool_result", { id: "t1", content: "out", blocked: false }, 3));
    const tool = s.items.find((i) => i.kind === "tool") as any;
    expect(tool).toMatchObject({ name: "bash", result: "out", blocked: false });
  });

  it("permission_request creates an unresolved card; done resets curAssistant", () => {
    let s = initialState();
    s = reduce(s, ev("token", { text: "x" }, 1));
    s = reduce(s, ev("permission_request", { request_id: "r1", reason: "bash" }, 2));
    expect(s.items.some((i) => i.kind === "permission")).toBe(true);
    s = reduce(s, ev("done", {}, 3));
    expect(s.curAssistant).toBeNull();
  });

  it("compacted and error produce notice / error items and reset curAssistant", () => {
    let s = initialState();
    s = reduce(s, ev("token", { text: "x" }, 1));
    s = reduce(s, ev("compacted", {}, 2));
    expect(s.curAssistant).toBeNull();
    s = reduce(s, ev("error", { error: "boom" }, 3));
    expect(s.items.some((i) => i.kind === "notice")).toBe(true);
    expect(s.items.some((i) => i.kind === "error")).toBe(true);
  });

  it("user message survives subsequent token events (regression)", () => {
    // Bug: submit() used to update only React state, not the reducer ref, so
    // the first token event recomputed items from a ref without the user
    // bubble and wiped it. reduceUser + reduce must keep it.
    let s = initialState();
    s = reduceUser(s, "hello");
    s = reduce(s, ev("token", { text: "hi" }, 1));
    s = reduce(s, ev("token", { text: "!" }, 2));
    s = reduce(s, ev("done", {}, 3));
    expect(s.items[0]).toMatchObject({ kind: "user", text: "hello" });
    expect(s.items[1]).toMatchObject({ kind: "assistant", text: "hi!" });
    expect(s.items).toHaveLength(2);
  });

  it("tokens after a tool_start land in a new bubble below the card (regression)", () => {
    let s = initialState();
    s = reduce(s, ev("token", { text: "Let me check" }, 1));
    s = reduce(s, ev("tool_start", { id: "t1", name: "read", input: {} }, 2));
    s = reduce(s, ev("tool_result", { id: "t1", content: "data", blocked: false }, 3));
    s = reduce(s, ev("token", { text: "It says data" }, 4));
    s = reduce(s, ev("done", {}, 5));
    expect(s.items).toHaveLength(3);
    expect(s.items[0]).toMatchObject({ kind: "assistant", text: "Let me check" });
    expect(s.items[1]).toMatchObject({ kind: "tool", name: "read" });
    expect(s.items[2]).toMatchObject({ kind: "assistant", text: "It says data" });
  });

  it("tokens after a text notice land in a new bubble (regression)", () => {
    let s = initialState();
    s = reduce(s, ev("token", { text: "partial" }, 1));
    s = reduce(s, ev("text", { text: "[max_tokens] retry" }, 2));
    s = reduce(s, ev("token", { text: "continued" }, 3));
    s = reduce(s, ev("done", {}, 4));
    expect(s.items).toHaveLength(3);
    expect(s.items[0]).toMatchObject({ kind: "assistant", text: "partial" });
    expect(s.items[1]).toMatchObject({ kind: "notice" });
    expect(s.items[2]).toMatchObject({ kind: "assistant", text: "continued" });
  });

  it("user replay event renders a user bubble and resets curAssistant", () => {
    let s = initialState();
    s = reduce(s, ev("user", { text: "hello" }, 1));
    s = reduce(s, ev("token", { text: "hi" }, 2));
    s = reduce(s, ev("done", {}, 3));
    expect(s.items).toHaveLength(2);
    expect(s.items[0]).toMatchObject({ kind: "user", text: "hello" });
    expect(s.items[1]).toMatchObject({ kind: "assistant", text: "hi" });
  });

  it("optimistic user bubble consumes its server echo (no duplicate)", () => {
    // Bug: submit() pushed a user bubble via reduceUser AND the gateway emitted
    // a live `user` event on post_message, so the sender saw two copies. The
    // reducer now records the optimistic text and consumes one matching echo.
    let s = initialState();
    s = reduceUser(s, "hello");
    s = reduce(s, ev("user", { text: "hello" }, 1));   // server echo → consumed
    s = reduce(s, ev("token", { text: "hi" }, 2));
    s = reduce(s, ev("done", {}, 3));
    expect(s.items).toHaveLength(2);
    expect(s.items[0]).toMatchObject({ kind: "user", text: "hello" });
    expect(s.items[1]).toMatchObject({ kind: "assistant", text: "hi" });
    expect(s.pendingUser).toBeNull();
  });

  it("a second identical user message still renders (consume is one-shot)", () => {
    let s = initialState();
    s = reduceUser(s, "hi"); s = reduce(s, ev("user", { text: "hi" }, 1));
    s = reduceUser(s, "hi"); s = reduce(s, ev("user", { text: "hi" }, 2));
    s = reduce(s, ev("done", {}, 3));
    const userMsgs = s.items.filter((i) => i.kind === "user");
    expect(userMsgs).toHaveLength(2);
  });

  it("rebuilds items from replay frames (history reconstruction)", () => {
    // Reconnect replays past events; items must rebuild so the conversation
    // renders. (Status is transient — not in the reducer — so replay can't
    // animate or strand the indicator; see the applyLiveActivity suite.)
    let s = initialState();
    s = reduce(s, ev("user", { text: "hi" }, 1));
    s = reduce(s, ev("token", { text: "Let me" }, 2));
    s = reduce(s, ev("tool_start", { id: "t1", name: "bash", input: {} }, 3));
    s = reduce(s, ev("tool_result", { id: "t1", content: "out", blocked: false }, 4));
    s = reduce(s, ev("token", { text: "done" }, 5));
    expect(s.items.length).toBeGreaterThanOrEqual(4);
  });
});

describe("applyLiveActivity (transient status)", () => {
  it("tracks the live activity across a multi-round turn", () => {
    let a = initialActivity;
    a = applyLiveActivity(a, ev("token", { text: "Let me" }, 1));
    expect(a.status.kind).toBe("replying");
    a = applyLiveActivity(a, ev("tool_start", { id: "t1", name: "bash", input: {} }, 2));
    expect(a.status).toMatchObject({ kind: "tool", toolName: "bash", toolCount: 1, round: 0 });
    a = applyLiveActivity(a, ev("tool_result", { id: "t1", content: "out", blocked: false }, 3));
    expect(a.status).toMatchObject({ kind: "thinking", toolCount: 1, round: 1 });
    a = applyLiveActivity(a, ev("tool_start", { id: "t2", name: "read_file", input: {} }, 4));
    expect(a.status).toMatchObject({ kind: "tool", toolName: "read_file", toolCount: 2, round: 1 });
    a = applyLiveActivity(a, ev("tool_result", { id: "t2", content: "data", blocked: false }, 5));
    a = applyLiveActivity(a, ev("token", { text: "done" }, 6));
    expect(a.status.kind).toBe("replying");
    a = applyLiveActivity(a, ev("done", {}, 7));
    expect(a.status.kind).toBe("idle");
    expect(a.inFlight).toBe(false);
  });

  it("surfaces permission wait and resets on done/error", () => {
    let a = initialActivity;
    a = applyLiveActivity(a, ev("permission_request", { request_id: "r1", reason: "bash" }, 1));
    expect(a.status.kind).toBe("permission");
    a = applyLiveActivity(a, ev("error", { error: "denied" }, 2));
    expect(a.status.kind).toBe("idle");
    expect(a.inFlight).toBe(false);
  });

  it("leaves activity untouched for user / task_notification / memory", () => {
    let a = { status: { kind: "replying" as const, toolName: null, toolCount: 0, round: 1 }, inFlight: true };
    a = applyLiveActivity(a, ev("user", { text: "x" }, 1));
    a = applyLiveActivity(a, ev("task_notification", { task_id: "b1" }, 2));
    a = applyLiveActivity(a, ev("memory", {}, 3));
    expect(a.status.kind).toBe("replying");
    expect(a.inFlight).toBe(true);
  });
});
