"use client";
// useWebSocket — bind the method-routed webClient to the zustand stores.
//
// On mount (or when sessionId changes): connect webClient to /ws with
// ?session_id=&last_seq=, then register event handlers that route each dotted
// event into chatStore. Exposes connection state + a `send` helper that fires
// chat.send and a `request` passthrough for panels (config/skills/agents/…).
//
// This is the new-architecture counterpart to lib/useAgentTransport.ts; the
// legacy ChatPanel still uses useAgentTransport until it's migrated to the
// multi-panel shell.
import { useCallback, useEffect, useRef, useState } from 'react';
import { webClient, webRequest } from './services/webClient';
import { ReqMethod, type WebConnectionState } from './types/websocket';
import { useChatStore } from './stores/chatStore';
import { WsEventName } from './stores/chatStore';

export interface UseWebSocket {
  state: WebConnectionState;
  ready: boolean;
  /** Send a user message to the bound session via chat.send. */
  send: (text: string) => Promise<void>;
  /** Interrupt the current turn (intent: cancel). */
  interrupt: (intent?: 'cancel' | 'pause' | 'resume') => Promise<void>;
  /** Resolve the pending ask_user_question / permission prompt. */
  answer: (allow: boolean) => Promise<void>;
  /** Passthrough to webClient.request for non-chat panels. */
  request: typeof webRequest;
}

export function useWebSocket(sessionId: string | null): UseWebSocket {
  const [state, setState] = useState<WebConnectionState>(webClient.getState());
  // lastSeq ref so a reconnect resumes from the highest seq we've applied.
  const lastSeqRef = useRef(0);

  // Keep lastSeqRef in sync with the store.
  const storeLastSeq = useChatStore((s) => s.lastSeq);
  useEffect(() => { lastSeqRef.current = storeLastSeq; }, [storeLastSeq]);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    let unsubState: (() => void) | undefined;
    const unsubs: Array<() => void> = [];

    (async () => {
      unsubState = webClient.onStateChange((s) => { if (!cancelled) setState(s); });

      // Bind event handlers → chatStore. Each handler reads the payload and
      // forwards; the store dedups by seq.
      const cs = useChatStore.getState();
      unsubs.push(webClient.on(WsEventName.CHAT_DELTA, (e) =>
        cs.onDelta(e.payload as any, e.seq)));
      unsubs.push(webClient.on(WsEventName.CHAT_TOOL_CALL, (e) =>
        cs.onToolCall(e.payload as any, e.seq)));
      unsubs.push(webClient.on(WsEventName.CHAT_TOOL_RESULT, (e) =>
        cs.onToolResult(e.payload as any, e.seq)));
      unsubs.push(webClient.on(WsEventName.CHAT_ASK_USER_QUESTION, (e) =>
        cs.onAskUserQuestion(e.payload as any, e.seq)));
      unsubs.push(webClient.on(WsEventName.CHAT_FINAL, (e) => cs.onFinal(e.seq)));
      unsubs.push(webClient.on(WsEventName.CHAT_ERROR, (e) =>
        cs.onError(e.payload as any, e.seq)));
      unsubs.push(webClient.on(WsEventName.CHAT_USER, (e) =>
        cs.onUser(e.payload as any, e.seq)));
      unsubs.push(webClient.on(WsEventName.CHAT_NOTICE, (e) =>
        cs.onNotice((e.payload as any)?.text || '', e.seq)));
      unsubs.push(webClient.on(WsEventName.CHAT_COMPACTED, (e) => cs.onCompacted(e.seq)));
      unsubs.push(webClient.on(WsEventName.CHAT_TASK_NOTIFICATION, (e) =>
        cs.onTaskNotification(e.payload as any, e.seq)));

      try {
        await webClient.connect({
          sessionId,
          lastSeq: lastSeqRef.current,
        });
      } catch {
        // webClient schedules a reconnect; state will flip to 'reconnecting'.
      }
    })();

    return () => {
      cancelled = true;
      unsubState?.();
      unsubs.forEach((u) => u());
      // Leave the socket open so a quick sessionId swap reuses it; the
      // webClient reconnect logic handles teardown on real unmount via
      // disconnect() in a higher-level cleanup if needed.
    };
  }, [sessionId]);

  // Disconnect on full unmount (component using the hook goes away).
  useEffect(() => {
    return () => { void webClient.disconnect('unmount'); };
  }, []);

  const send = useCallback(async (text: string) => {
    if (!sessionId) return;
    useChatStore.getState().sendUser(text);
    await webRequest(ReqMethod.CHAT_SEND, { session_id: sessionId, content: text });
  }, [sessionId]);

  const interrupt = useCallback(async (intent: 'cancel' | 'pause' | 'resume' = 'cancel') => {
    if (!sessionId) return;
    await webRequest(ReqMethod.CHAT_INTERRUPT, { session_id: sessionId, intent });
  }, [sessionId]);

  const answer = useCallback(async (allow: boolean) => {
    const q = useChatStore.getState().pendingQuestion;
    if (!q || !sessionId) return;
    await webRequest(ReqMethod.CHAT_USER_ANSWER, {
      session_id: sessionId,
      request_id: q.request_id,
      answers: [{ allow }],
    });
    useChatStore.getState().resolveQuestion(allow);
  }, [sessionId]);

  return { state, ready: state === 'ready', send, interrupt, answer, request: webRequest };
}
