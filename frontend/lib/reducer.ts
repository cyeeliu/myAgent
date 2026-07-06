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
  lastSeq: number;             // highest event seq processed — dedupes across
                               // duplicate WS delivery (StrictMode double-mount,
                               // replay reprocess, etc.)
  inFlight: boolean;           // a turn is running on the server — gates the Send
                               // button so a second message can't 409 on
                               // "a turn is already in flight".
}

export function initialState(): ReducerState {
  return { items: [], curAssistant: null, lastSeq: 0, inFlight: false };
}

// Append a user-authored message. Must go through the reducer (not just React
// state) so the next event doesn't recompute items from a stale ref and wipe
// the user bubble. Sending marks the turn in-flight until the server emits done/error.
export function reduceUser(state: ReducerState, text: string): ReducerState {
  return {
    items: [...state.items, { kind: "user", text }],
    curAssistant: null,
    lastSeq: state.lastSeq,
    inFlight: true,
  };
}

// Pure event reducer (spec §11.6). ChatPanel delegates to this so the token /
// tool / permission / done / error / compacted transitions are unit-testable
// without rendering.
export function reduce(state: ReducerState, e: AgentEvent): ReducerState {
  // Dedupe by monotonic seq: a second WS (StrictMode dev double-mount) or a
  // replay reprocess can redeliver the same event. Skip if already seen.
  const seq = (e as any).seq || 0;
  if (seq > 0 && seq <= state.lastSeq) return state;
  const lastSeq = Math.max(state.lastSeq, seq);
  const items = [...state.items];
  let curAssistant = state.curAssistant;
  let inFlight = state.inFlight;
  switch (e.kind) {
    case "user":
      // Server-replayed user bubble (session hydration). Live user messages go
      // through reduceUser instead; this kind only appears in replay frames.
      curAssistant = null;
      items.push({ kind: "user", text: e.payload.text || "" });
      break;
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
      // A tool call starts a new segment — reset curAssistant so the next
      // assistant turn (after tool_result) gets a fresh bubble below the
      // card, instead of appending to the pre-tool bubble and rendering out
      // of order.
      curAssistant = null;
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
      inFlight = false;
      break;
    case "done":
      curAssistant = null;
      inFlight = false;
      break;
    case "text":
      // Inter-round notices ([max_tokens] retry, [cron inject], …) act as
      // segment separators too — reset so subsequent tokens start a new bubble.
      curAssistant = null;
      items.push({ kind: "notice", text: e.payload.text || "" });
      break;
  }
  return { items, curAssistant, lastSeq, inFlight };
}
