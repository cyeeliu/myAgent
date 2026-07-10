// WebSocket wire types — mirror the gateway /ws protocol exactly.
//   client → server: { type: 'req', id, method, params }
//   server → client: { type: 'res', id, ok, payload, error? }
//                  | { type: 'event', event, payload, seq?, stream_id? }
// The method names line up with agent_gateway.common.schema.message.ReqMethod;
// the event names line up with agent_gateway.gateway_push.wire._KIND_MAP.

export type WebConnectionState =
  | 'idle'
  | 'connecting'
  | 'ready'
  | 'reconnecting'
  | 'closed';

export interface WsRequest {
  type: 'req';
  id: string;
  method: string;
  params?: Record<string, unknown>;
}

export interface WsResponse {
  type: 'res';
  id: string;
  ok: boolean;
  payload?: unknown;
  error?: string;
  code?: string;
}

export interface WsEvent {
  type: 'event';
  event: string;
  payload: Record<string, unknown>;
  seq?: number;
  stream_id?: string;
}

export type WebMessage = WsRequest | WsResponse | WsEvent;

export interface WebRequestOptions {
  timeoutMs?: number;
  signal?: AbortSignal;
}

/** Connect-time options. `sessionId` binds the socket to a session and sets the
 * `?session_id=` / `?last_seq=` query so the gateway replays missed events. */
export interface WebConnectOptions {
  sessionId?: string;
  lastSeq?: number;
  provider?: string;
  apiKey?: string;
  apiBase?: string;
  model?: string;
}

export interface WebError extends Error {
  code?: string;
  requestId?: string;
  retriable?: boolean;
}

// ── RPC method names (mirror ReqMethod enum) ──
export const ReqMethod = {
  CHAT_SEND: 'chat.send',
  CHAT_INTERRUPT: 'chat.interrupt',
  CHAT_USER_ANSWER: 'chat.user_answer',
  SESSION_LIST: 'session.list',
  SESSION_CREATE: 'session.create',
  SESSION_SWITCH: 'session.switch',
  SESSION_DELETE: 'session.delete',
  SESSION_RENAME: 'session.rename',
  SESSION_STATUS: 'session.status',
  HISTORY_GET: 'history.get',
  CONFIG_GET: 'config.get',
  CONFIG_SET: 'config.set',
  MODELS_LIST: 'models.list',
  MODELS_REPLACE_ALL: 'models.replace_all',
  SKILLS_LIST: 'skills.list',
  SKILLS_INSTALLED: 'skills.installed',
  SKILLS_GET: 'skills.get',
  AGENTS_LIST: 'agents.list',
  AGENTS_GET: 'agents.get',
  AGENTS_CREATE: 'agents.create',
  AGENTS_UPDATE: 'agents.update',
  AGENTS_DELETE: 'agents.delete',
  PATH_GET: 'path.get',
  FILES_LIST: 'files.list',
  TTS_SYNTHESIZE: 'tts.synthesize',
  COMMAND_COMPACT: 'command.compact',
  COMMAND_CONTEXT: 'command.context',
  COMMAND_MODEL: 'command.model',
  CHANNEL_GET: 'channel.get',
  HEARTBEAT_PING: 'heartbeat.ping',
} as const;

// ── Event names (mirror gateway_push.wire._KIND_MAP) ──
export const WsEventName = {
  CHAT_DELTA: 'chat.delta',
  CHAT_NOTICE: 'chat.notice',
  CHAT_TOOL_CALL: 'chat.tool_call',
  CHAT_TOOL_RESULT: 'chat.tool_result',
  CHAT_ERROR: 'chat.error',
  CHAT_ASK_USER_QUESTION: 'chat.ask_user_question',
  CHAT_COMPACTED: 'chat.compacted',
  CHAT_FINAL: 'chat.final',
  CHAT_USER: 'chat.user',
  CHAT_TASK_NOTIFICATION: 'chat.task_notification',
  CHAT_MEMORY: 'chat.memory',
  HEARTBEAT: 'heartbeat',
} as const;

// ── Event payload shapes ──
export interface ChatDeltaPayload {
  text?: string;
  replay?: boolean;
}

export interface ChatToolCallPayload {
  id?: string;
  name?: string;
  input?: unknown;
  replay?: boolean;
}

export interface ChatToolResultPayload {
  id?: string;
  content?: string;
  blocked?: boolean;
  replay?: boolean;
}

export interface ChatAskUserQuestionPayload {
  request_id?: string;
  questions: Array<{ question: string; detail?: string }>;
  source?: string;
}

export interface ChatErrorPayload {
  error?: string;
  code?: string;
}

export interface ChatUserPayload {
  text?: string;
  replay?: boolean;
}

export interface ChatTaskNotificationPayload {
  task_id?: string;
  command?: string;
  exit_code?: number | null;
  summary?: string;
}

export interface HeartbeatPayload {
  t?: number;
}
