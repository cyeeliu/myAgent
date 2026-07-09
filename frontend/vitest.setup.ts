// Minimal mocks for browser APIs the transports use. Each test installs its
// own behaviour via the `__mock` handles exposed on globalThis.
import { vi } from "vitest";

// React 18 act() needs this flag to suppress the "testing environment not
// configured to support act(...)" warning under jsdom.
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

// --- WebSocket ---
type WsListener = (ev: any) => void;
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  url: string;
  readyState = 0;
  onopen: WsListener | null = null;
  onmessage: WsListener | null = null;
  onerror: WsListener | null = null;
  onclose: WsListener | null = null;
  sent: string[] = [];
  constructor(url: string) { this.url = url; MockWebSocket.instances.push(this); }
  send(data: string) { this.sent.push(data); }
  close() { this.readyState = 3; this.onclose?.({} as any); }
  // helpers for tests
  _open() { this.readyState = 1; this.onopen?.({} as any); }
  _recv(data: string) { this.onmessage?.({ data } as any); }
  _err() { this.readyState = 3; this.onerror?.({} as any); }
}
(globalThis as any).WebSocket = MockWebSocket;
(globalThis as any).__MockWebSocket = MockWebSocket;

// --- EventSource ---
type EsListener = (ev: any) => void;
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: EsListener | null = null;
  onmessage: EsListener | null = null;
  readyState = 0;
  private listeners: Record<string, EsListener[]> = {};
  constructor(url: string) { this.url = url; MockEventSource.instances.push(this); }
  addEventListener(type: string, fn: EsListener) { (this.listeners[type] ||= []).push(fn); }
  close() { this.readyState = 2; }
  _open() { this.readyState = 1; this.onopen?.({} as any); }
  _dispatch(type: string, data: string) {
    if (type === "message") this.onmessage?.({ data } as any);
    (this.listeners[type] || []).forEach((fn) => fn({ data } as any));
  }
}
(globalThis as any).EventSource = MockEventSource;
(globalThis as any).__MockEventSource = MockEventSource;

// --- fetch ---
(globalThis as any).fetch = vi.fn();
