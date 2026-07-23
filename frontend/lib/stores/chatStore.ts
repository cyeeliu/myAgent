// chatStore — conversation state driven by the gateway's dotted event stream.
// Mirrors myagent's chatStore shape (messages + toolExecutions + streaming
// buffer + processing flags) but fed by myAgent's event names:
//   chat.delta → append token to the streaming assistant message
//   chat.tool_call → register a ToolExecution (pending)
//   chat.tool_result → resolve the matching ToolExecution
//   chat.ask_user_question → surface a pending permission/ask prompt
//   chat.final / chat.error → end the turn
//   chat.user → echo a user bubble (replay hydration)
//   chat.compacted / chat.notice / chat.task_notification → notice bubbles
//
// The store is intentionally self-contained: useWebSocket binds webClient.on(...)
// handlers to these actions. Dedup by monotonic seq so a reconnect replay or a
// StrictMode double-mount can't double-apply frames.
import { create } from 'zustand';
import {
  type Message,
  type ToolExecution,
  type ToolCall,
  type ToolResult,
} from '../types/message';
import {
  WsEventName,
  type ChatAskUserQuestionPayload,
  type ChatDeltaPayload,
  type ChatToolCallPayload,
  type ChatToolResultPayload,
  type ChatUserPayload,
  type ChatErrorPayload,
  type ChatTaskNotificationPayload,
} from '../types/websocket';
import { useTodoStore } from './todoStore';

const TOOL_TIMEOUT_MS = 12_000_000;

function nowIso(): string { return new Date().toISOString(); }
function timeoutAt(baseIso: string): string {
  return new Date(Date.parse(baseIso) + TOOL_TIMEOUT_MS).toISOString();
}
function genId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export interface PendingQuestion {
  request_id: string;
  question: string;
  detail?: string;
  source?: string;
  resolved?: boolean;
}

interface ChatState {
  messages: Message[];
  isProcessing: boolean;
  isThinking: boolean;
  currentStreamId: string | null;
  toolExecutions: Map<string, ToolExecution>;
  toolExecutionOrder: string[];
  lastSeq: number;
  pendingQuestion: PendingQuestion | null;
  error: string | null;

  // Event-driven actions (called by useWebSocket handlers).
  onDelta: (payload: ChatDeltaPayload, seq?: number) => void;
  onToolCall: (payload: ChatToolCallPayload, seq?: number) => void;
  onToolResult: (payload: ChatToolResultPayload, seq?: number) => void;
  onAskUserQuestion: (payload: ChatAskUserQuestionPayload, seq?: number) => void;
  onFinal: (seq?: number) => void;
  onError: (payload: ChatErrorPayload, seq?: number) => void;
  onUser: (payload: ChatUserPayload, seq?: number) => void;
  onNotice: (text: string, seq?: number) => void;
  onCompacted: (seq?: number) => void;
  onTaskNotification: (payload: ChatTaskNotificationPayload, seq?: number) => void;

  // User-driven actions.
  sendUser: (text: string) => void;
  resolveQuestion: (allow: boolean) => void;
  clear: () => void;
  /** Drop a seq we've already seen (returns true if the caller should skip). */
  seen: (seq?: number) => boolean;
}

function makeAssistant(): Message {
  return { id: genId('a'), role: 'assistant', content: '', timestamp: nowIso(), isStreaming: true };
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  isProcessing: false,
  isThinking: false,
  currentStreamId: null,
  toolExecutions: new Map(),
  toolExecutionOrder: [],
  lastSeq: 0,
  pendingQuestion: null,
  error: null,

  seen: (seq) => {
    if (!seq || seq <= 0) return false;
    if (seq <= get().lastSeq) return true;
    set({ lastSeq: Math.max(get().lastSeq, seq) });
    return false;
  },

  onDelta: (payload, seq) => {
    if (get().seen(seq)) return;
    const text = payload.text || '';
    const replay = payload.replay;
    let { currentStreamId, messages } = get();
    if (!currentStreamId) {
      const msg = makeAssistant();
      messages = [...messages, msg];
      currentStreamId = msg.id;
      set({ messages, currentStreamId });
    }
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === currentStreamId ? { ...m, content: m.content + text } : m),
      isProcessing: true,
      isThinking: false,
    }));
    if (replay) return;
  },

  onToolCall: (payload, seq) => {
    if (get().seen(seq)) return;
    const id = payload.id || genId('tool');
    const toolCall: ToolCall = { id, name: payload.name || 'tool', input: payload.input };
    const startedAt = nowIso();
    const exec: ToolExecution = {
      toolCallId: id, toolCall, status: 'pending',
      startedAt, updatedAt: startedAt, timeoutAt: timeoutAt(startedAt),
    };
    set((s) => {
      if (s.toolExecutions.has(id)) return s;
      const execs = new Map(s.toolExecutions).set(id, exec);
      return {
        toolExecutions: execs,
        toolExecutionOrder: [...s.toolExecutionOrder, id],
        isProcessing: true,
        // A tool call interrupts the streaming bubble; next delta starts a new one.
        currentStreamId: null,
      };
    });
  },

  onToolResult: (payload, seq) => {
    if (get().seen(seq)) return;
    const id = payload.id || '';
    if (!id) return;
    const result: ToolResult = {
      toolCallId: id,
      result: payload.content || '',
      success: !payload.blocked,
      blocked: payload.blocked,
    };
    set((s) => {
      const existing = s.toolExecutions.get(id);
      if (!existing) return s;
      const execs = new Map(s.toolExecutions).set(id, {
        ...existing, result, status: result.success ? 'completed' : 'error',
        updatedAt: nowIso(),
      });
      return { toolExecutions: execs };
    });
  },

  onAskUserQuestion: (payload, seq) => {
    if (get().seen(seq)) return;
    const q = payload.questions?.[0];
    set({
      pendingQuestion: {
        request_id: payload.request_id || '',
        question: q?.question || 'tool',
        detail: q?.detail,
        source: payload.source,
      },
      isProcessing: true,
    });
  },

  onFinal: (seq) => {
    if (get().seen(seq)) return;
    const { currentStreamId, messages } = get();
    set({
      isProcessing: false,
      isThinking: false,
      currentStreamId: null,
      messages: currentStreamId
        ? messages.map((m) => m.id === currentStreamId ? { ...m, isStreaming: false } : m)
        : messages,
    });
  },

  onError: (payload, seq) => {
    if (get().seen(seq)) return;
    const text = payload.error || 'error';
    set((s) => ({
      error: text,
      isProcessing: false,
      isThinking: false,
      currentStreamId: null,
      messages: [...s.messages, {
        id: genId('err'), role: 'assistant', content: text, timestamp: nowIso(),
      }],
    }));
  },

  onUser: (payload, seq) => {
    if (get().seen(seq)) return;
    const text = payload.text || '';
    set((s) => ({
      messages: [...s.messages, {
        id: genId('u'), role: 'user', content: text, timestamp: nowIso(),
      }],
    }));
  },

  onNotice: (text, seq) => {
    if (get().seen(seq)) return;
    set((s) => ({
      messages: [...s.messages, {
        id: genId('n'), role: 'assistant', content: text, timestamp: nowIso(),
      }],
      currentStreamId: null,
    }));
  },

  onCompacted: (seq) => {
    if (get().seen(seq)) return;
    get().onNotice('[context compacted]');
  },

  onTaskNotification: (payload, seq) => {
    if (get().seen(seq)) return;
    const id = payload.task_id ? ` ${payload.task_id}` : '';
    const cmd = payload.command ? ` ${payload.command}` : '';
    const code = payload.exit_code != null ? ` exit=${payload.exit_code}` : '';
    const summary = payload.summary ? ` → ${payload.summary}` : '';
    get().onNotice(`[background${id}${cmd} 完成${code}${summary}]`);
  },

  sendUser: (text) => {
    set((s) => ({
      messages: [...s.messages, {
        id: genId('u'), role: 'user', content: text, timestamp: nowIso(),
      }],
      isProcessing: true,
      isThinking: true,
      error: null,
    }));
  },

  resolveQuestion: (allow) => {
    set((s) => ({
      pendingQuestion: s.pendingQuestion ? { ...s.pendingQuestion, resolved: true } : null,
    }));
    void allow;
  },

  clear: () => {
    set({
      messages: [], isProcessing: false, isThinking: false,
      currentStreamId: null, toolExecutions: new Map(), toolExecutionOrder: [],
      lastSeq: 0, pendingQuestion: null, error: null,
    });
    useTodoStore.getState().clear();
  },
}));

// Re-export event names so useWebSocket can bind without a separate import.
export { WsEventName };
