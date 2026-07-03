import { describe, it, expect } from "vitest";
import { reduce, reduceUser, initialState } from "./reducer";

const ev = (kind: any, payload: any, seq = 1) => ({ kind, payload, seq });

describe("reducer", () => {
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
});
