import { create } from 'zustand';

interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

interface SpanEvent {
  event: 'start' | 'end';
  span_id: string;
  name: string;
  duration_ms?: number;
  tokens_in?: number;
  tokens_out?: number;
  tool?: string;
  timestamp: number;
}

interface CompactionEvent {
  reason: string;
  timestamp: number;
}

interface MemoryEvent {
  extracted: string;
  timestamp: number;
}

interface ChatObservabilityState {
  tokenUsage: TokenUsage | null;
  totalTokensIn: number;
  totalTokensOut: number;
  spanEvents: SpanEvent[];
  compactionEvents: CompactionEvent[];
  memoryEvents: MemoryEvent[];
  contextUsage: { tokens_used: number; context_max: number; rate: number } | null;
  toolCallCount: number;
  turnCount: number;

  onTokenUsage: (usage: TokenUsage) => void;
  onSpanEvent: (span: Omit<SpanEvent, 'timestamp'>) => void;
  onCompaction: (reason: string) => void;
  onMemory: (extracted: string) => void;
  onContextUsage: (data: { tokens_used: number; context_max: number; rate: number }) => void;
  incrementTurn: () => void;
  reset: () => void;
}

const MAX_EVENTS = 200;

export const useChatObservabilityStore = create<ChatObservabilityState>((set) => ({
  tokenUsage: null,
  totalTokensIn: 0,
  totalTokensOut: 0,
  spanEvents: [],
  compactionEvents: [],
  memoryEvents: [],
  contextUsage: null,
  toolCallCount: 0,
  turnCount: 0,

  onTokenUsage: (usage) =>
    set((s) => ({
      tokenUsage: usage,
      totalTokensIn: s.totalTokensIn + usage.prompt_tokens,
      totalTokensOut: s.totalTokensOut + usage.completion_tokens,
    })),

  onSpanEvent: (span) =>
    set((s) => {
      const events = [...s.spanEvents, { ...span, timestamp: Date.now() }];
      if (events.length > MAX_EVENTS) events.shift();
      return {
        spanEvents: events,
        toolCallCount: span.name === 'tool_call' && span.event === 'end'
          ? s.toolCallCount + 1
          : s.toolCallCount,
      };
    }),

  onCompaction: (reason) =>
    set((s) => ({
      compactionEvents: [...s.compactionEvents, { reason, timestamp: Date.now() }].slice(-MAX_EVENTS),
    })),

  onMemory: (extracted) =>
    set((s) => ({
      memoryEvents: [...s.memoryEvents, { extracted, timestamp: Date.now() }].slice(-MAX_EVENTS),
    })),

  onContextUsage: (data) => set({ contextUsage: data }),

  incrementTurn: () => set((s) => ({ turnCount: s.turnCount + 1 })),

  reset: () =>
    set({
      tokenUsage: null,
      totalTokensIn: 0,
      totalTokensOut: 0,
      spanEvents: [],
      compactionEvents: [],
      memoryEvents: [],
      contextUsage: null,
      toolCallCount: 0,
      turnCount: 0,
    }),
}));
