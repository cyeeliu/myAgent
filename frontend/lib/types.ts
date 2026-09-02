// Event + message types — mirror the gateway's event enum exactly (spec §4.1).
// No parallel taxonomy: kinds are token|text|tool_start|tool_result|error|
// permission_request|compacted|done (plus `ping` heartbeats, ignored by the UI).

export type EventKind =
  | "token" | "text" | "tool_start" | "tool_result"
  | "error" | "permission_request" | "compacted" | "done" | "ping" | "user"
  | "task_notification" | "memory";

export interface AgentEvent {
  seq: number;
  kind: EventKind;
  payload: Record<string, any>;
}

// Client → server messages (WS) / REST bodies (SSE).
export type AgentClientMsg =
  | { type: "user_message"; text: string }
  | { type: "permission_response"; request_id: string; allow: boolean; modify?: string }
  | { type: "interrupt" }
  | { type: "resume"; last_seq: number };

export interface Transport {
  connect(): Promise<void>;
  disconnect(): void;
  send(msg: AgentClientMsg): void;
  onEvent(cb: (e: AgentEvent) => void): void;
  readonly lastSeq: number;
}
