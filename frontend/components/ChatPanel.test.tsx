import { describe, it, expect, beforeEach, vi } from "vitest";
import { createRoot } from "react-dom/client";
import { act } from "react";
import type { AgentEvent } from "../lib/types";

// Controllable transport mock: tests drive events via __emit and read sends
// via __sent. Avoids the real WebSocket/EventSource plumbing.
let __emit: ((e: AgentEvent) => void) | null = null;
const __sent: any[] = [];
let __connected = false;
vi.mock("../lib/useAgentTransport", () => ({
  useAgentTransport: (_sid: string | null, onEvent: (e: AgentEvent) => void) => {
    __emit = onEvent;
    return {
      transport: "ws" as const,
      connected: __connected,
      send: (m: any) => __sent.push(m),
      interrupt: () => __sent.push({ type: "interrupt" }),
    };
  },
}));

import { ChatPanel } from "./ChatPanel";

function render(sid: string | null) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => { root.render(<ChatPanel sessionId={sid} />); });
  return { container, root };
}

function setInputValue(el: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, "value",
  )!.set!;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function pressEnter(el: HTMLTextAreaElement) {
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
}

function sendButton(): HTMLButtonElement {
  return document.querySelector('button[title="发送"]') as HTMLButtonElement;
}

describe("ChatPanel session switch", () => {
  beforeEach(() => {
    __emit = null;
    __sent.length = 0;
    __connected = true;
    document.body.innerHTML = "";
  });

  it("resets inFlight + status when switching sessions (regression)", () => {
    const { container, root } = render("A");

    // Type + Enter triggers submit() → inFlight=true, status=thinking → Send
    // disabled. This is the live send path, not a replayed `user` event.
    const input = container.querySelector("textarea") as HTMLTextAreaElement;
    act(() => { setInputValue(input, "hi"); });
    act(() => { pressEnter(input); });
    expect(__sent.some((m) => m.type === "user_message")).toBe(true);
    expect(sendButton().disabled).toBe(true); // inFlight=true on session A

    // Switch to session B (idle). The reducer ref is reset; the React inFlight/
    // status mirrors MUST be reset too, else Send stays disabled and the status
    // bar shows stale "正在回复".
    act(() => { root.render(<ChatPanel sessionId={"B"} />); });
    // Type into B; Send must be enabled (inFlight reset to false). If inFlight
    // were stale from session A, Send would stay disabled even with input.
    const inputB = document.querySelector("textarea") as HTMLTextAreaElement;
    act(() => { setInputValue(inputB, "hello"); });
    expect(sendButton().disabled).toBe(false);

    root.unmount();
  });

  it("seeds inFlight from server worker_alive on reconnect mid-turn", async () => {
    // Simulate reconnecting to a session whose turn is already running on the
    // server. The replay stream has no "turn started" marker, so the client
    // must consult /status and seed inFlight=true itself.
    const fetchMock = (globalThis as any).fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ worker_alive: true }) });

    const { container, root } = render("A");
    // Give the composer input so Send's disabled state reflects only inFlight
    // (not the empty-input gate).
    const ta = container.querySelector("textarea") as HTMLTextAreaElement;
    act(() => { setInputValue(ta, "x"); });
    // The status fetch fires on connect; flush its microtask resolution inside
    // act so the resulting setInFlight/setStatus are captured.
    await act(async () => {
      for (let i = 0; i < 5; i++) await Promise.resolve();
    });
    expect(sendButton().disabled).toBe(true); // inFlight seeded from worker_alive

    // When the turn finishes, `done` clears inFlight and Send re-enables.
    act(() => { __emit!({ seq: 1, kind: "done", payload: {} }); });
    expect(sendButton().disabled).toBe(false);

    fetchMock.mockReset();
    root.unmount();
  });

  it("replay frames rebuild items but never show the status bar", () => {
    // The bug: switching into a session replayed history through the reducer,
    // which animated the status bar (正在回复/调用工具/思考中) and stranded it
    // non-idle. Status is now transient — replay must not touch it.
    const fetchMock = (globalThis as any).fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ worker_alive: false }) });

    const { root } = render("A");
    const statusText = () => document.body.textContent || "";
    const LIVE = ["思考中", "正在回复", "调用工具", "等待授权"];

    // Replay a full historical turn (marked replay). Items rebuild; the status
    // bar must stay hidden throughout — no flicker, no stranded state.
    act(() => {
      __emit!({ seq: 1, kind: "user", payload: { text: "hi", replay: true } });
      __emit!({ seq: 2, kind: "token", payload: { text: "Let me", replay: true } });
      __emit!({ seq: 3, kind: "tool_start", payload: { id: "t1", name: "bash", input: {}, replay: true } });
      __emit!({ seq: 4, kind: "tool_result", payload: { id: "t1", content: "out", blocked: false, replay: true } });
      __emit!({ seq: 5, kind: "token", payload: { text: "done", replay: true } });
    });
    expect(LIVE.some((l) => statusText().includes(l))).toBe(false);

    root.unmount();
  });
});
