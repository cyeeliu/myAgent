// WebClient — method-routed WebSocket client for the gateway /ws endpoint.
// Adapted from myagent's webClient for the myAgent Next.js frontend:
//   - process.env.NODE_ENV instead of import.meta.env (Next.js, not Vite)
//   - no /__dev/ws-log endpoint (myAgent has no such route)
//   - connects to /ws with ?session_id=&last_seq= for replay resume
//
// Wire protocol:
//   out: { type: 'req', id, method, params }
//   in : { type: 'res', id, ok, payload, error? } | { type: 'event', event, payload, seq? }
//
// `request(method, params)` returns a promise that resolves with the `res`
// payload; events are fan-out to registered handlers via `on(eventName, cb)`.
import {
  WebConnectOptions,
  WebConnectionState,
  WebError,
  WebRequestOptions,
  WsEvent,
  WsRequest,
  WsResponse,
} from '../types/websocket';
import { getWsBase } from './env';
import i18n from '../i18n';

type EventHandler = (event: WsEvent) => void;
type TypedEventHandler<TPayload> = (event: WsEvent & { payload: TPayload }) => void;
type StateHandler = (state: WebConnectionState) => void;

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (reason?: unknown) => void;
  timeoutId: ReturnType<typeof setTimeout>;
}

const MAX_RECONNECT_ATTEMPTS = 5;
const DEFAULT_TIMEOUT_MS = 15000;
const isDev = process.env.NODE_ENV !== 'production';

class WebClient {
  private ws: WebSocket | null = null;
  private state: WebConnectionState = 'idle';
  private handlers = new Map<string, Set<EventHandler>>();
  private stateHandlers = new Set<StateHandler>();
  private pending = new Map<string, PendingRequest>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private manualClose = false;
  private connectPromise: Promise<void> | null = null;
  private lastConnectOptions: WebConnectOptions = {};
  // The session_id the current socket is bound to (via ?session_id=). The
  // gateway starts the outbound event drain only for the session bound at
  // connect time, so switching sessions MUST reopen the socket — connect()
  // uses this to detect a session change and force a reconnect instead of
  // no-oping on an already-open socket.
  private boundSessionId: string | undefined = undefined;
  private requestSeq = 0;

  getState(): WebConnectionState {
    return this.state;
  }

  getInflightCount(): number {
    return this.pending.size;
  }

  onStateChange(handler: StateHandler): () => void {
    this.stateHandlers.add(handler);
    return () => { this.stateHandlers.delete(handler); };
  }

  on<TPayload = Record<string, unknown>>(
    eventName: string,
    handler: TypedEventHandler<TPayload>,
  ): () => void {
    const set = this.handlers.get(eventName) ?? new Set<EventHandler>();
    set.add(handler as EventHandler);
    this.handlers.set(eventName, set);
    return () => {
      const target = this.handlers.get(eventName);
      if (!target) return;
      target.delete(handler as EventHandler);
      if (target.size === 0) this.handlers.delete(eventName);
    };
  }

  async connect(options: WebConnectOptions = {}): Promise<void> {
    // If the socket is already open AND bound to the requested session, reuse it.
    // A session change MUST reopen — the gateway drains events only for the
    // session bound at connect time, so an existing socket bound to a different
    // (or no) session can't deliver the new session's stream.
    if (this.ws?.readyState === WebSocket.OPEN && options.sessionId === this.boundSessionId) {
      return;
    }
    if (this.connectPromise && options.sessionId === this.boundSessionId) {
      return this.connectPromise;
    }
    // Session change (or first bind) while open: close the old socket first.
    if (this.ws && this.ws.readyState !== WebSocket.CLOSED) {
      this.manualClose = true; // suppress reconnect storm from the close handler
      try { this.ws.close(1000, 'session switch'); } catch { /* ignore */ }
      this.ws = null;
      this.manualClose = false;
    }

    this.lastConnectOptions = options;
    this.manualClose = false;
    this.updateState(this.reconnectAttempts > 0 ? 'reconnecting' : 'connecting');

    const url = this.buildWsUrl(options);

    this.connectPromise = new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(url);
      this.ws = ws;

      ws.onopen = () => {
        this.boundSessionId = options.sessionId;
        this.reconnectAttempts = 0;
        this.updateState('ready');
        this.connectPromise = null;
        resolve();
      };

      ws.onmessage = (event) => { this.handleIncoming(event.data); };

      ws.onerror = () => {
        const error = this.createWebError(
          i18n.t('network.wsError'), 'WS_ERROR', undefined, true);
        this.connectPromise = null;
        if (this.state !== 'ready') reject(error);
      };

      ws.onclose = (closeEvent) => {
        this.ws = null;
        this.connectPromise = null;
        this.rejectAllPending(
          this.createWebError(
            i18n.t('network.connectionClosedWithCode', { code: closeEvent.code }),
            'WS_DISCONNECTED', undefined, true));
        if (this.manualClose || closeEvent.code === 1000) {
          this.updateState('closed');
          return;
        }
        this.scheduleReconnect();
      };
    });

    return this.connectPromise;
  }

  disconnect(reason = 'User disconnect'): Promise<void> {
    this.manualClose = true;
    this.clearReconnectTimer();
    this.rejectAllPending(
      this.createWebError(i18n.t('network.connectionClosed'), 'WS_CLOSED', undefined, false));
    const currentWs = this.ws;
    let closedPromise: Promise<void> = Promise.resolve();
    if (currentWs) {
      closedPromise = new Promise<void>((resolve) => {
        let finished = false;
        const finish = () => { if (!finished) { finished = true; resolve(); } };
        const timeoutId = setTimeout(finish, 800);
        currentWs.addEventListener('close', () => {
          clearTimeout(timeoutId);
          finish();
        }, { once: true });
        currentWs.close(1000, reason);
      });
    }
    this.ws = null;
    this.connectPromise = null;
    this.updateState('closed');
    return closedPromise;
  }

  async request<T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    options: WebRequestOptions = {},
  ): Promise<T> {
    await this.ensureReady();
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw this.createWebError(
        i18n.t('network.connectionUnavailable'), 'WS_NOT_READY', undefined, true);
    }

    const id = this.generateRequestId();
    const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const message: WsRequest = { type: 'req', id, method, params: params ?? {} };

    return new Promise<T>((resolve, reject) => {
      const timeoutId = setTimeout(() => {
        this.pending.delete(id);
        reject(this.createWebError(
          i18n.t('network.requestTimeout'), 'REQUEST_TIMEOUT', id, true));
      }, timeoutMs);

      const pending: PendingRequest = {
        resolve: (value) => resolve(value as T),
        reject,
        timeoutId,
      };
      this.pending.set(id, pending);

      if (options.signal) {
        const onAbort = () => {
          if (!this.pending.has(id)) return;
          clearTimeout(timeoutId);
          this.pending.delete(id);
          reject(this.createWebError(
            i18n.t('network.requestAborted'), 'REQUEST_ABORTED', id, false));
        };
        if (options.signal.aborted) { onAbort(); return; }
        options.signal.addEventListener('abort', onAbort, { once: true });
      }

      if (isDev) {
        // eslint-disable-next-line no-console
        console.debug('[ws→]', message);
      }
      this.ws?.send(JSON.stringify(message));
    });
  }

  private async ensureReady(): Promise<void> {
    if (this.ws?.readyState === WebSocket.OPEN && this.state === 'ready') return;
    await this.connect(this.lastConnectOptions);
  }

  private handleIncoming(rawData: string): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(rawData);
    } catch {
      return;
    }
    const message = this.normalizeIncoming(parsed);
    if (!message) return;

    if (isDev) {
      // eslint-disable-next-line no-console
      console.debug('[ws←]', message);
    }

    if (message.type === 'res') {
      this.resolvePending(message);
      return;
    }
    this.dispatchEvent(message);
  }

  private normalizeIncoming(input: unknown): WsResponse | WsEvent | null {
    if (!input || typeof input !== 'object') return null;
    const msg = input as Record<string, unknown>;
    const rawType = msg.type;
    if (rawType === 'res') {
      if (typeof msg.id !== 'string') return null;
      return {
        type: 'res',
        id: msg.id,
        ok: Boolean(msg.ok),
        payload: msg.payload,
        error: typeof msg.error === 'string' ? msg.error : undefined,
        code: typeof msg.code === 'string' ? msg.code : undefined,
      };
    }
    if (rawType === 'event') {
      const eventName = typeof msg.event === 'string' ? msg.event : '';
      if (!eventName) return null;
      return {
        type: 'event',
        event: eventName,
        payload: this.normalizePayload(msg.payload),
        seq: typeof msg.seq === 'number' ? msg.seq : undefined,
        stream_id: typeof msg.stream_id === 'string' ? msg.stream_id : undefined,
      };
    }
    return null;
  }

  private normalizePayload(payload: unknown): Record<string, unknown> {
    if (!payload || typeof payload !== 'object') return {};
    return payload as Record<string, unknown>;
  }

  private resolvePending(message: WsResponse): void {
    const pending = this.pending.get(message.id);
    if (!pending) return;
    clearTimeout(pending.timeoutId);
    this.pending.delete(message.id);
    if (message.ok) {
      pending.resolve(message.payload);
      return;
    }
    pending.reject(this.createWebError(
      message.error ?? i18n.t('network.requestFailed'),
      message.code, message.id, this.isRetriableCode(message.code)));
  }

  private dispatchEvent(event: WsEvent): void {
    const handlers = this.handlers.get(event.event);
    if (!handlers || handlers.size === 0) return;
    handlers.forEach((handler) => handler(event));
  }

  private scheduleReconnect(): void {
    this.clearReconnectTimer();
    this.reconnectAttempts += 1;
    this.updateState('reconnecting');
    // Exponential backoff for the first N attempts, then a steady 2s retry so
    // the client auto-recovers once the gateway comes back.
    const delay =
      this.reconnectAttempts <= MAX_RECONNECT_ATTEMPTS
        ? Math.min(1000 * 2 ** (this.reconnectAttempts - 1), 30000)
        : 2000;
    this.reconnectTimer = setTimeout(() => {
      void this.connect(this.lastConnectOptions);
    }, delay);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private rejectAllPending(error: WebError): void {
    this.pending.forEach((entry) => {
      clearTimeout(entry.timeoutId);
      entry.reject(error);
    });
    this.pending.clear();
  }

  private updateState(state: WebConnectionState): void {
    this.state = state;
    this.stateHandlers.forEach((handler) => handler(state));
  }

  private buildWsUrl(options: WebConnectOptions): string {
    const base = getWsBase();
    const path = base.endsWith('/ws') ? '' : '/ws';
    const params = new URLSearchParams();
    if (options.sessionId) params.set('session_id', options.sessionId);
    if (options.lastSeq && options.lastSeq > 0) params.set('last_seq', String(options.lastSeq));
    if (options.provider) params.set('provider', options.provider);
    if (options.apiKey) params.set('api_key', options.apiKey);
    if (options.apiBase) params.set('api_base', options.apiBase);
    if (options.model) params.set('model', options.model);
    const query = params.toString();
    const target = `${base}${path}`;
    return query ? `${target}?${query}` : target;
  }

  private generateRequestId(): string {
    this.requestSeq += 1;
    const stamp = Date.now().toString(36);
    return `req_${stamp}_${this.requestSeq}`;
  }

  private createWebError(
    message: string, code?: string, requestId?: string, retriable = false,
  ): WebError {
    const error = new Error(message) as WebError;
    error.code = code;
    error.requestId = requestId;
    error.retriable = retriable;
    return error;
  }

  private isRetriableCode(code?: string): boolean {
    return code === 'REQUEST_TIMEOUT' || code === 'WS_DISCONNECTED' || code === 'WS_NOT_READY';
  }
}

export const webClient = new WebClient();

/** Convenience: fire a method-routed request and await its payload. */
export async function webRequest<T = unknown>(
  method: string,
  params?: Record<string, unknown>,
  options?: WebRequestOptions,
): Promise<T> {
  return webClient.request<T>(method, params, options);
}

if (isDev && typeof window !== 'undefined') {
  (window as unknown as Record<string, unknown>).webClient = webClient;
}

export type { WsEvent };
