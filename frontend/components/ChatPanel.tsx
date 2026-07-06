"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAgentTransport } from "../lib/useAgentTransport";
import { ToolCard } from "./ToolCard";
import { PermissionCard } from "./PermissionCard";
import { reduce, reduceUser, initialState, type Item } from "../lib/reducer";
import type { AgentEvent } from "../lib/types";

export function ChatPanel({ sessionId }: { sessionId: string | null }) {
  const [items, setItems] = useState<Item[]>([]);
  const [inFlight, setInFlight] = useState(false);
  const [input, setInput] = useState("");
  // Reducer state (curAssistant index) lives in a ref so token appends are O(1).
  const stateRef = useRef(initialState());

  // Auto-scroll: stick to the bottom while the user is there. If they scroll
  // up, stop auto-scrolling and surface a ↓ button to jump back down.
  const scrollRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
  }, []);

  useEffect(() => { if (atBottom) scrollToBottom(); }, [items, atBottom, scrollToBottom]);

  // Reset the conversation when the session changes — the transport reconnects
  // with last_seq=0 and the server replays the new session's buffered events,
  // which the reducer rebuilds from an empty slate.
  useEffect(() => {
    stateRef.current = initialState();
    setItems([]);
    setAtBottom(true);
  }, [sessionId]);

  const onEvent = useCallback((e: AgentEvent) => {
    const next = reduce(stateRef.current, e);
    stateRef.current = next;
    setItems(next.items);
    setInFlight(next.inFlight);
  }, []);

  const { transport, connected, send, interrupt } = useAgentTransport(sessionId, onEvent);

  const submit = () => {
    const text = input.trim();
    if (!text || !connected || inFlight) return;
    // Optimistically mark the turn in-flight so the Send button disables
    // immediately and a second click can't 409 on "a turn is already in flight".
    // The server emits a `user` event back over the transport; the reducer
    // (onEvent) adds the bubble from that, keeping the ref (source of truth)
    // driven solely by events — no optimistic add that could duplicate.
    stateRef.current = { ...stateRef.current, inFlight: true };
    setInFlight(true);
    send({ type: "user_message", text });
    setInput("");
  };

  const respondPermission = (rid: string, allow: boolean) => {
    send({ type: "permission_response", request_id: rid, allow });
    setItems((p) => p.map((it) =>
      it.kind === "permission" && (it as any).rid === rid ? { ...it, resolved: true } as Item : it));
  };

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-2 text-sm text-zinc-400">
        <span>myAgent</span>
        <span>session: {sessionId?.slice(0, 8) ?? "…"} · {transport ?? "connecting"} · {connected ? "live" : "…"}</span>
        <button className="rounded bg-zinc-800 px-2 py-1 hover:bg-zinc-700" onClick={interrupt}>Interrupt</button>
      </header>

      <div className="relative flex-1">
        <div ref={scrollRef} onScroll={onScroll}
             className="absolute inset-0 space-y-2 overflow-y-auto px-4 py-4">
          {items.map((it, i) => {
            if (it.kind === "user") return <div key={i} className="text-right"><span className="inline-block rounded bg-zinc-800 px-3 py-2">{it.text}</span></div>;
            if (it.kind === "assistant") {
              const text = it.text.replace(/\s+$/, "");
              if (!text.trim()) return null;  // drop blank/whitespace-only bubbles (e.g. between tool calls)
              return <div key={i} className="whitespace-pre-wrap text-zinc-100">{text}</div>;
            }
            if (it.kind === "tool") return <ToolCard key={i} name={it.name} input={it.input} result={it.result} blocked={it.blocked} />;
            if (it.kind === "permission") return !it.resolved ? <PermissionCard key={i} reason={it.reason} detail={it.detail} onRespond={(a) => respondPermission(it.rid, a)} /> : null;
            if (it.kind === "notice") return <div key={i} className="text-xs text-zinc-500">{it.text}</div>;
            if (it.kind === "error") return <div key={i} className="rounded bg-red-950/50 px-3 py-2 text-red-300">{it.text}</div>;
            return null;
          })}
        </div>
        {!atBottom && (
          <button
            onClick={scrollToBottom}
            className="absolute bottom-4 right-4 flex h-9 w-9 items-center justify-center rounded-full border border-zinc-700 bg-zinc-800 text-zinc-200 shadow-lg hover:bg-zinc-700"
            title="scroll to bottom"
          >↓</button>
        )}
      </div>

      <div className="flex gap-2 border-t border-zinc-800 p-3">
        <input className="flex-1 rounded bg-zinc-900 px-3 py-2 outline-none ring-zinc-700 focus:ring-1"
               value={input} onChange={(e) => setInput(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && submit()}
               placeholder={!connected ? "connecting…" : inFlight ? "turn in progress…" : "Ask myAgent…"} />
        <button className="rounded bg-cyan-700 px-4 py-2 hover:bg-cyan-600" onClick={submit} disabled={!connected || inFlight}>Send</button>
      </div>
    </div>
  );
}
