"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAgentTransport } from "../lib/useAgentTransport";
import { ToolCard } from "./ToolCard";
import { PermissionCard } from "./PermissionCard";
import { reduce, reduceUser, initialState, type Item } from "../lib/reducer";
import type { AgentEvent } from "../lib/types";

export function ChatPanel({ sessionId }: { sessionId: string | null }) {
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  // Reducer state (curAssistant index) lives in a ref so token appends are O(1).
  const stateRef = useRef(initialState());

  // Reset the conversation when the session changes — the transport reconnects
  // with last_seq=0 and the server replays the new session's buffered events,
  // which the reducer rebuilds from an empty slate.
  useEffect(() => {
    stateRef.current = initialState();
    setItems([]);
  }, [sessionId]);

  const onEvent = useCallback((e: AgentEvent) => {
    const next = reduce(stateRef.current, e);
    stateRef.current = next;
    setItems(next.items);
  }, []);

  const { transport, connected, send, interrupt } = useAgentTransport(sessionId, onEvent);

  const submit = () => {
    const text = input.trim();
    if (!text || !connected) return;
    // Route the user message through the reducer so the ref (source of truth)
    // and React state stay in sync — otherwise the next token event recomputes
    // items from a stale ref and wipes the user bubble.
    const next = reduceUser(stateRef.current, text);
    stateRef.current = next;
    setItems(next.items);
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

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {items.map((it, i) => {
          if (it.kind === "user") return <div key={i} className="mb-2 text-right"><span className="inline-block rounded bg-zinc-800 px-3 py-2">{it.text}</span></div>;
          if (it.kind === "assistant") return <div key={i} className="mb-2 whitespace-pre-wrap text-zinc-100">{it.text}</div>;
          if (it.kind === "tool") return <ToolCard key={i} name={it.name} input={it.input} result={it.result} blocked={it.blocked} />;
          if (it.kind === "permission") return !it.resolved ? <PermissionCard key={i} reason={it.reason} detail={it.detail} onRespond={(a) => respondPermission(it.rid, a)} /> : null;
          if (it.kind === "notice") return <div key={i} className="mb-2 text-xs text-zinc-500">{it.text}</div>;
          if (it.kind === "error") return <div key={i} className="mb-2 rounded bg-red-950/50 px-3 py-2 text-red-300">{it.text}</div>;
          return null;
        })}
      </div>

      <div className="flex gap-2 border-t border-zinc-800 p-3">
        <input className="flex-1 rounded bg-zinc-900 px-3 py-2 outline-none ring-zinc-700 focus:ring-1"
               value={input} onChange={(e) => setInput(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && submit()}
               placeholder={connected ? "Ask myAgent…" : "connecting…"} />
        <button className="rounded bg-cyan-700 px-4 py-2 hover:bg-cyan-600" onClick={submit} disabled={!connected}>Send</button>
      </div>
    </div>
  );
}
