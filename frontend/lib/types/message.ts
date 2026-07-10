// Domain message types — shapes returned by the gateway RPC methods and held in
// the zustand stores. Mirror agent_gateway.common.e2a.agent_compat payloads.

export type AgentMode = 'agent.fast' | 'agent.plan' | 'team' | 'auto_harness';
export type SessionStatus = 'active' | 'paused' | 'completed' | 'interrupted';

export interface SessionMeta {
  session_id: string;
  transport: string;
  created_at: number;
  last_activity: number;
  title: string;
  history_len: number;
}

export interface SessionStatusInfo {
  session_id: string;
  transport: string;
  active_sinks: string[];
  last_seq: number;
  buffered: number;
  worker_alive: boolean;
  history_len: number;
}

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export interface ToolCall {
  id: string;
  name: string;
  input: unknown;
}

export interface ToolResult {
  toolCallId: string;
  result: string;
  success: boolean;
  blocked?: boolean;
  summary?: string;
}

export type ToolExecutionStatus = 'pending' | 'completed' | 'error' | 'timeout';

export interface ToolExecution {
  toolCallId: string;
  toolCall: ToolCall;
  result?: ToolResult;
  status: ToolExecutionStatus;
  startedAt: string;
  updatedAt: string;
  timeoutAt: string;
  requestId?: string;
  resultArrivedAfterTimeout?: boolean;
  timedOutAt?: string;
}

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  isStreaming?: boolean;
  toolExecutions?: ToolExecution[];
  usageSummary?: unknown;
  fileItems?: unknown[];
}

export interface SkillEntry {
  name: string;
  description?: string;
}

export interface AgentEntry {
  name: string;
  description?: string;
  prompt?: string;
  model?: string;
  tools?: string[];
}

export interface ModelEntry {
  model_name: string;
  api_base?: string;
  api_key?: string;
  model_provider?: string;
  is_default?: boolean;
  alias?: string;
}

export interface ConfigInfo {
  model_id: string;
  base_url?: string;
  api_key_masked?: string;
  fallback_model?: string;
  a2ui_enabled?: boolean;
}

export interface TodoItem {
  id: string;
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  activeForm?: string;
}
