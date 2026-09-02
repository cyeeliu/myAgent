import type { AgentEvent } from "./types";

export type Item =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; id: string; name: string; input: any; result?: string; blocked?: boolean }
  | { kind: "permission"; rid: string; reason: string; detail?: string; resolved?: boolean }
  | { kind: "notice"; text: string }
  | { kind: "error"; text: string };

// Live activity the agent is performing right now. Drives the status bar at the
// bottom of the chat. This is TRANSIENT — it is NOT part of the reducer state
// (which rebuilds from replayed history) and is never driven by replay frames.
// It lives in ChatPanel as a separate piece of state updated only by live
// events + the live send + the server's worker_alive on reconnect. Keeping it
// out of the reducer means loading a session's history can never animate or
// strand the indicator on a past state.
export type StatusKind = "idle" | "thinking" | "replying" | "tool" | "permission";

export interface Status {
  kind: StatusKind;
  toolName: string | null; // current tool call (when kind === "tool")
  toolCount: number;       // tools invoked so far this turn (cumulative)
  round: number;           // model round within the turn (1 = first LLM call)
}

export const initialStatus: Status = { kind: "idle", toolName: null, toolCount: 0, round: 0 };

// The reducer state is the CONVERSATION (items + where the next token lands +
// dedup cursor). It is rebuilt from replayed history on reconnect. Live-activity
// status/inFlight are deliberately not here.
export interface ReducerState {
  items: Item[];
  curAssistant: number | null; // index of the assistant bubble accumulating tokens
  lastSeq: number;             // highest event seq processed — dedupes across
                               // duplicate WS delivery (StrictMode double-mount,
                               // replay reprocess, etc.)
  pendingUser: string | null;  // text of the last optimistic user bubble awaiting
                               // its server echo. The gateway emits a live `user`
                               // event on post_message (needed so OTHER clients
                               // reconnecting rebuild the bubble); the sender
                               // already added it via reduceUser, so the matching
                               // echo is consumed once to avoid a duplicate.
}

export function initialState(): ReducerState {
  return { items: [], curAssistant: null, lastSeq: 0, pendingUser: null };
}

// Append a user-authored message. Must go through the reducer (not just React
// state) so the next event doesn't recompute items from a stale ref and wipe
// the user bubble. Does NOT touch live activity — ChatPanel sets that on send.
// Records the text as pending so the server's echoing `user` event is consumed
// instead of pushing a second copy.
export function reduceUser(state: ReducerState, text: string): ReducerState {
  return {
    items: [...state.items, { kind: "user", text }],
    curAssistant: null,
    lastSeq: state.lastSeq,
    pendingUser: text,
  };
}

// Pure event reducer (spec §11.6). ChatPanel delegates to this so the token /
// tool / permission / done / error / compacted item transitions are unit-
// testable without rendering. Rebuilds items for BOTH replay and live events
// (history reconstruction and live streaming share the same item logic).
export function reduce(state: ReducerState, e: AgentEvent): ReducerState {
  // Dedupe by monotonic seq: a second WS (StrictMode dev double-mount) or a
  // replay reprocess can redeliver the same event. Skip if already seen.
  const seq = (e as any).seq || 0;
  if (seq > 0 && seq <= state.lastSeq) return state;
  const lastSeq = Math.max(state.lastSeq, seq);
  const items = [...state.items];
  let curAssistant = state.curAssistant;
  switch (e.kind) {
    case "user": {
      // The gateway emits `user` both for replay (reconnect hydration) AND live
      // on post_message (so other tabs rebuild the bubble). The sender already
      // added the bubble optimistically via reduceUser — consume the matching
      // echo once instead of pushing a duplicate.
      const text = e.payload.text || "";
      if (state.pendingUser !== null && state.pendingUser === text) {
        return { items, curAssistant: null, lastSeq, pendingUser: null };
      }
      curAssistant = null;
      items.push({ kind: "user", text });
      break;
    }
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
      break;
    case "done":
      curAssistant = null;
      break;
    case "text":
      // Inter-round notices ([max_tokens] retry, [cron inject], …) act as
      // segment separators too — reset so subsequent tokens start a new bubble.
      curAssistant = null;
      items.push({ kind: "notice", text: e.payload.text || "" });
      break;
    case "task_notification": {
      // A background task finished. Surface a concise heads-up; the full output
      // is delivered to the model (injected as a user message) and the agent's
      // follow-up turn renders the real summary. Keep this notice short.
      const p = e.payload || {};
      const id = p.task_id ? ` ${p.task_id}` : "";
      const cmd = p.command ? ` ${p.command}` : "";
      const code = p.exit_code != null ? ` exit=${p.exit_code}` : "";
      const summary = p.summary ? ` → ${p.summary}` : "";
      items.push({ kind: "notice", text: `[background${id}${cmd} 完成${code}${summary}]` });
      break;
    }
    case "memory":
      // Memory extraction wrote files in the background — informational only.
      break;
  }
  return { items, curAssistant, lastSeq, pendingUser: state.pendingUser };
}

// Transient live-activity state: the status bar + the Send-gate. Updated ONLY
// by live events (never replay) and the live send. Pulled out of the reducer so
// replayed history can't animate or strand the indicator.
export interface Activity {
  status: Status;
  inFlight: boolean; // a turn is running on the server — gates Send so a second
                     // message can't 409 on "a turn is already in flight".
}

export const initialActivity: Activity = { status: initialStatus, inFlight: false };

// Advance the live activity by one LIVE event. Returns the next activity.
// ChatPanel calls this only for non-replay events. `done`/`error` clear the
// turn; tool/token/permission transition the indicator; user/task_notification/
// memory leave it untouched.
export function applyLiveActivity(prev: Activity, e: AgentEvent): Activity {
  let { status, inFlight } = prev;
  switch (e.kind) {
    case "token":
      status = { ...status, kind: "replying" };
      break;
    case "tool_start":
      status = { kind: "tool", toolName: e.payload.name, toolCount: status.toolCount + 1, round: status.round };
      break;
    case "tool_result":
      // Tool finished — the model will think again before the next token/tool.
      status = { kind: "thinking", toolName: null, toolCount: status.toolCount, round: status.round + 1 };
      break;
    case "permission_request":
      status = { ...status, kind: "permission" };
      break;
    case "compacted":
    case "text":
      status = { ...status, kind: "thinking" };
      break;
    case "error":
    case "done":
      status = initialStatus;
      inFlight = false;
      break;
    // user, task_notification, memory, ping: no activity change.
  }
  return { status, inFlight };
}
