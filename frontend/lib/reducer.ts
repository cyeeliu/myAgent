import type { AgentEvent } from "./types";

export type Item =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; id: string; name: string; input: any; result?: string; blocked?: boolean }
  | { kind: "permission"; rid: string; reason: string; detail?: string; resolved?: boolean }
  | { kind: "notice"; text: string }
  | { kind: "error"; text: string };

export interface ReducerState {
  items: Item[];
  curAssistant: number | null; // index of the assistant bubble accumulating tokens
}

export function initialState(): ReducerState {
  return { items: [], curAssistant: null };
}

// Append a user-authored message. Must go through the reducer (not just React
// state) so the next event doesn't recompute items from a stale ref and wipe
// the user bubble.
export function reduceUser(state: ReducerState, text: string): ReducerState {
  return {
    items: [...state.items, { kind: "user", text }],
    curAssistant: null,
  };
}

// Pure event reducer (spec §11.6). ChatPanel delegates to this so the token /
// tool / permission / done / error / compacted transitions are unit-testable
// without rendering.
export function reduce(state: ReducerState, e: AgentEvent): ReducerState {
  const items = [...state.items];
  let curAssistant = state.curAssistant;
  switch (e.kind) {
    case "token": {
      if (curAssistant === null) {
        items.push({ kind: "assistant", text: "" });
        curAssistant = items.length - 1;
      }
      items[curAssistant] = {
        kind: "assistant",
        text: (items[curAssistant] as any).text + (e.payload.text || ""),
      };
      break;
    }
    case "tool_start":
      items.push({ kind: "tool", id: e.payload.id, name: e.payload.name, input: e.payload.input });
      break;
    case "tool_result": {
      const i = items.findIndex((it) => it.kind === "tool" && (it as any).id === e.payload.id);
      if (i >= 0) items[i] = { ...items[i], result: e.payload.content, blocked: e.payload.blocked } as Item;
      break;
    }
    case "permission_request":
      items.push({
        kind: "permission",
        rid: e.payload.request_id || e.payload.rid || String(e.payload.seq),
        reason: e.payload.reason || "tool",
        detail: e.payload.detail,
      });
      break;
    case "compacted":
      items.push({ kind: "notice", text: "[context compacted]" });
      curAssistant = null;
      break;
    case "error":
      items.push({ kind: "error", text: e.payload.error || "error" });
      break;
    case "done":
      curAssistant = null;
      break;
    case "text":
      items.push({ kind: "notice", text: e.payload.text || "" });
      break;
  }
  return { items, curAssistant };
}
